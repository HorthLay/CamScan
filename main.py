from contextlib import asynccontextmanager
import logging
import os
import platform
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2
import numpy as np

from database import create_tables, engine as database_engine
from routers.registration import router as registration_router
from routers.camera import router as camera_router
from routers.detection import router as detection_router
from routers.cctv import router as cctv_router
from services.capture_service import get_camera, read_camera_frame, release_camera
from services import camera_control

logger = logging.getLogger("camscan.main")

# ── Platform-aware camera backend ────────────────────────────────────────────
# Windows MSMF (default) frequently hangs on VideoCapture(). DirectShow works.
_IS_WINDOWS = platform.system() == "Windows"
_CAMERA_BACKEND = cv2.CAP_DSHOW if _IS_WINDOWS else cv2.CAP_ANY

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()                         # create all tables on startup
    try:
        get_camera()
    except Exception:
        print("Warning: Cannot open webcam.")
    yield
    release_camera()


from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CamScan – Face Recognition API", lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/snapshots", StaticFiles(directory="snapshots"), name="snapshots")

app.include_router(registration_router)
app.include_router(camera_router)
app.include_router(detection_router)
app.include_router(cctv_router)


# ── Live face stream ─────────────────────────────────────────────────────────
# Primary detection: UniFace (SCRFD, ONNX) — boxes carry confidence scores.
# Fallback: fast Haar-cascade if the UniFace engine can't be loaded.

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

_STREAM_MAX_WIDTH = 640
_STREAM_JPEG_Q    = 60   # lower quality = much faster encoding, plenty for a live feed
_DETECT_EVERY_N   = 2    # run detection at half rate; reuse last boxes in between

_uniface_stream_ready = None   # None = untested, True/False after first attempt


def _auto_resize(frame: np.ndarray) -> np.ndarray:
    """Shrink wide frames so the stream stays cheap to detect + encode."""
    h, w = frame.shape[:2]
    if w <= _STREAM_MAX_WIDTH:
        return frame
    scale = _STREAM_MAX_WIDTH / w
    return cv2.resize(frame, (_STREAM_MAX_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)


def _detect_faces(frame: np.ndarray):
    """Return [(x1, y1, x2, y2, label, color), ...] for the current frame."""
    global _uniface_stream_ready

    if _uniface_stream_ready is not False:
        try:
            from services.uniface_service import detect_faces

            results = []
            for det in detect_faces(frame):
                x1, y1, x2, y2 = det["bbox"]
                conf = det["confidence"]
                color = (0, 255, 0) if conf >= 0.75 else (0, 200, 255)
                results.append((x1, y1, x2, y2, f"Face {conf:.0%}", color))
            _uniface_stream_ready = True
            return results
        except Exception:
            _uniface_stream_ready = False

    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    boxes = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
    return [(x, y, x + w, y + h, "Face", (0, 255, 0)) for (x, y, w, h) in boxes]


def _draw_faces(frame: np.ndarray, faces) -> None:
    for x1, y1, x2, y2, label, color in faces:
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty = max(y1 - 8, th + 6)
        cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 6, ty + 4), color, -1)
        cv2.putText(frame, label, (x1 + 3, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)


def generate_frames(source: Optional[str] = None, camera: Optional[cv2.VideoCapture] = None):
    last_faces = []
    frame_idx  = 0
    consecutive_failures = 0
    MAX_FAILURES = 30  # give up after 30 consecutive bad reads
    while True:
        try:
            if camera is None:
                frame = read_camera_frame()
            else:
                ok, frame = camera.read()
                if not ok or frame is None:
                    consecutive_failures += 1
                    logger.warning("Camera read failed (%d/%d)", consecutive_failures, MAX_FAILURES)
                    if consecutive_failures >= MAX_FAILURES:
                        logger.error("Too many consecutive camera read failures — stopping stream.")
                        break
                    continue
        except Exception as exc:
            consecutive_failures += 1
            logger.warning("Frame read error (%d/%d): %s", consecutive_failures, MAX_FAILURES, exc)
            if consecutive_failures >= MAX_FAILURES:
                logger.error("Too many consecutive frame errors — stopping stream.")
                break
            continue

        consecutive_failures = 0
        frame = _auto_resize(frame)
        if frame_idx % _DETECT_EVERY_N == 0:
            last_faces = _detect_faces(frame)
        frame_idx += 1

        _draw_faces(frame, last_faces)

        ret, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_JPEG_Q]
        )
        if ret:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

@app.get("/")
def home():
    return {"status": "running", "message": "CamScan Face Recognition API"}


@app.get("/detection", include_in_schema=False)
def detection_page():
    """Serve the CCTV detection dashboard."""
    from fastapi.responses import HTMLResponse
    from pathlib import Path
    html = Path("detection.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/health")
def health():
    """Quick liveness probe: DB reachability + camera + model status."""
    from sqlalchemy import text as sql_text
    from services.capture_service import camera_available

    db_status = "up"
    try:
        with database_engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
    except Exception:
        db_status = "down"

    face_backend = "unknown"
    face_ready = False
    try:
        from services.face_service import engine_status
        status = engine_status()
        face_backend = status["backend"]
        face_ready = bool(status["ready"])
    except Exception:
        pass

    liveness = False
    try:
        from services.uniface_service import liveness_enabled
        liveness = liveness_enabled()
    except Exception:
        pass

    is_healthy = db_status == "up"
    return {
        "status":       "ok" if is_healthy else "degraded",
        "database":     db_status,
        "camera":       "up" if camera_available() else "down",
        "face_model":   face_ready,
        "face_backend": face_backend,
        "liveness":     liveness,
    }


@app.get("/video_feed")
def video_feed(stream_url: Optional[str] = None):
    # Explicit stream_url (CCTV/RTSP/http) wins; otherwise use the active
    # camera-control source; otherwise fall back to the local webcam.
    source = stream_url or camera_control.current_source()

    camera: Optional[cv2.VideoCapture] = None
    if source:
        logger.info("Opening video source: %s", source)
        # Use DirectShow on Windows for local numeric sources too
        camera = cv2.VideoCapture(source, _CAMERA_BACKEND) if _IS_WINDOWS else cv2.VideoCapture(source)
        if not camera.isOpened():
            logger.error("Cannot open stream: %s", source)
            camera = None
            raise HTTPException(status_code=503, detail=f"Cannot open stream: {source}")

    def generate():
        try:
            yield from generate_frames(source, camera)
        finally:
            if camera is not None:
                camera.release()

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Camera WebSocket ─────────────────────────────────────────────────────────
# Mirror of the REST camera API over WS: start/stop/get_status/clear_stop/
# get_active_ips with request/response ids. Broadcasts status_update and
# ip_status_changed to every connected panel so the Active Users table stays
# in sync across browsers. Protocol matches public/js/websocket-camera.js.

_ws_clients: dict = {}
_ws_clients_lock = threading.Lock()


async def _ws_send(ws: WebSocket, message: dict):
    try:
        await ws.send_json(message)
    except Exception:
        pass


async def _ws_broadcast(message: dict, exclude_ip: Optional[str] = None):
    targets = []
    with _ws_clients_lock:
        targets = [ws for ip, ws in _ws_clients.items() if ip != exclude_ip]
    for ws in targets:
        await _ws_send(ws, message)


async def _ws_broadcast_camera_state():
    snap = camera_control.snapshot()
    await _ws_broadcast({
        "action":    "status_update",
        "ip":        snap["ip"],
        "is_active": snap["is_active"],
        "is_stopped": snap["is_stopped"],
        "active_ips":  snap["active_ips"],
        "stopped_ips": snap["stopped_ips"],
        "stream_source": snap["stream_source"],
    })


@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket):
    await websocket.accept()
    ip = camera_control.ws_client_ip(websocket)

    with _ws_clients_lock:
        _ws_clients[ip] = websocket

    try:
        # Initial snapshot so the panel populates the Active Users table at once.
        snap = camera_control.snapshot()
        await _ws_send(websocket, {
            "action":    "status_update",
            "ip":        "",
            "is_active": bool(ip == snap["ip"] and snap["is_active"]),
            "is_stopped": False,
            "active_ips":  snap["active_ips"],
            "stopped_ips": snap["stopped_ips"],
            "stream_source": snap["stream_source"],
        })

        while True:
            message = await websocket.receive_json()
            action = message.get("action")
            request_id = message.get("request_id")

            if action == "ping":
                await _ws_send(websocket, {"action": "pong", "timestamp": message.get("timestamp")})
                continue

            if action == "get_status":
                res = camera_control.status(ip)
                await _ws_send(websocket, {"action": "response", "request_id": request_id, **res})
                continue

            if action == "start_camera":
                res = camera_control.start(ip, message.get("stream_url"))
                await _ws_send(websocket, {"action": "response", "request_id": request_id, **res})
                await _ws_broadcast_camera_state()
                await _ws_broadcast({
                    "action": "ip_status_changed",
                    "ip": ip,
                    "active_ips":  res["active_ips"],
                    "stopped_ips": res["stopped_ips"],
                    "stream_source": res["stream_source"],
                }, exclude_ip=ip)
                continue

            if action == "stop_camera":
                res = camera_control.stop(ip)
                await _ws_send(websocket, {"action": "response", "request_id": request_id, **res})
                await _ws_broadcast_camera_state()
                await _ws_broadcast({
                    "action": "ip_status_changed",
                    "ip": ip,
                    "active_ips":  res["active_ips"],
                    "stopped_ips": res["stopped_ips"],
                    "stream_source": res["stream_source"],
                }, exclude_ip=ip)
                continue

            if action == "clear_stop":
                res = camera_control.clear_stop(ip)
                await _ws_send(websocket, {"action": "response", "request_id": request_id, **res})
                await _ws_broadcast_camera_state()
                continue

            if action == "get_active_ips":
                res = camera_control.active_ips()
                await _ws_send(websocket, {
                    "action": "response", "request_id": request_id,
                    "success": True, **res,
                })
                continue

            await _ws_send(websocket, {
                "action": "error", "request_id": request_id,
                "error": f"Unknown action: {action}",
            })

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        with _ws_clients_lock:
            _ws_clients.pop(ip, None)
        # Auto-release on disconnect: frees the camera without blocking the IP.
        camera_control.release(ip)
        await _ws_broadcast_camera_state()
