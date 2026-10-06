import os
import sys
import unittest
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base, SessionLocal, get_db, engine
from main import app
from models import User
from services import camera_control


class CameraAndLookupTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        self.db = SessionLocal()
        self.user = User(name="Alice Doe", age=34, face_embeding="[0.1,0.2]")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        # Reset IP camera state between tests
        camera_control._active.clear()
        camera_control._stopped.clear()

    def tearDown(self):
        app.dependency_overrides.clear()
        camera_control._active.clear()
        camera_control._stopped.clear()
        self.db.close()
        Base.metadata.drop_all(bind=engine)

    # ── Camera control semantics ──────────────────────────────────────────

    def test_camera_start_and_single_owner(self):
        ok = camera_control.start("10.0.0.1")
        self.assertTrue(ok["success"])
        self.assertTrue(ok["is_active"])

        other = camera_control.start("10.0.0.2")
        self.assertFalse(other["success"])
        self.assertEqual(other["message"], "Camera is already in use by 10.0.0.1.")

    def test_camera_stop_blocks_until_clear(self):
        camera_control.start("10.0.0.1")
        stopped = camera_control.stop("10.0.0.1")
        self.assertTrue(stopped["is_stopped"])

        restart = camera_control.start("10.0.0.1")
        self.assertFalse(restart["success"])

        cleared = camera_control.clear_stop("10.0.0.1")
        self.assertFalse(cleared["is_stopped"])

        restart2 = camera_control.start("10.0.0.1")
        self.assertTrue(restart2["success"])

    def test_release_does_not_block_ip(self):
        camera_control.start("10.0.0.1")
        released = camera_control.release("10.0.0.1")
        self.assertTrue(released["is_allowed"])

        restart = camera_control.start("10.0.0.1")
        self.assertTrue(restart["success"])

    def test_active_ips_endpoint(self):
        r = self.client.get("/register/camera/active-ips")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"active_ips": [], "stopped_ips": []})

        camera_control.start("10.0.0.1")
        r = self.client.get("/register/camera/active-ips")
        self.assertIn("10.0.0.1", r.json()["active_ips"])

    def test_camera_source_endpoint(self):
        camera_control.start("10.0.0.1", "rtsp://192.168.0.10/stream")
        r = self.client.get("/camera/source")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stream_source"], "rtsp://192.168.0.10/stream")

    # ── Single-user lookup + verify flag ──────────────────────────────────

    def test_user_by_name_found(self):
        r = self.client.get("/register/users/by-name/alice doe")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["name"], "Alice Doe")
        self.assertTrue(data["face_verified"])
        self.assertEqual(data["id"], self.user.id)

    def test_user_by_name_not_found(self):
        r = self.client.get("/register/users/by-name/nobody")
        self.assertEqual(r.status_code, 404)

    def test_list_users_includes_face_verified(self):
        r = self.client.get("/register/users")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()[0]["face_verified"], True)

    # ── Detection endpoints surface, not camera-dependent ────────────────

    def test_detect_status_endpoint(self):
        r = self.client.get("/detect/status")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["is_detecting"], False)

    def test_detect_missing_payload_returns_400(self):
        r = self.client.post("/detect", json={})
        self.assertEqual(r.status_code, 400)

    # ── WebSocket camera protocol ──────────────────────────────────────────

    def test_ws_camera_ping_returns_pong(self):
        with self.client.websocket_connect("/ws/camera") as ws:
            first = ws.receive_json()
            self.assertEqual(first["action"], "status_update")
            ws.send_json({"action": "ping", "timestamp": 12345})
            msg = ws.receive_json()
            self.assertEqual(msg["action"], "pong")
            self.assertEqual(msg["timestamp"], 12345)

    def test_ws_camera_start_and_status_roundtrip(self):
        with self.client.websocket_connect("/ws/camera") as ws:
            ws.receive_json()  # drain initial status_update snapshot

            ws.send_json({"action": "get_status", "request_id": "r1"})
            status = ws.receive_json()
            self.assertEqual(status["request_id"], "r1")
            self.assertTrue(status["success"])
            self.assertTrue(status["is_allowed"])

            ws.send_json({"action": "start_camera", "request_id": "r2"})
            started = ws.receive_json()
            self.assertEqual(started["request_id"], "r2")
            self.assertTrue(started["success"])

            # start triggers a status_update broadcast to all sockets
            # (including our own); drain it before issuing the next request.
            extra = ws.receive_json()
            self.assertEqual(extra["action"], "status_update")

            ws.send_json({"action": "stop_camera", "request_id": "r3"})
            stopped = ws.receive_json()
            self.assertEqual(stopped["request_id"], "r3")
            self.assertTrue(stopped["success"])

    def test_ws_unknown_action_returns_error(self):
        with self.client.websocket_connect("/ws/camera") as ws:
            ws.receive_json()  # drain initial status_update snapshot
            ws.send_json({"action": "bogus", "request_id": "r9"})
            msg = ws.receive_json()
            self.assertEqual(msg["action"], "error")
            self.assertEqual(msg["request_id"], "r9")


if __name__ == "__main__":
    unittest.main()