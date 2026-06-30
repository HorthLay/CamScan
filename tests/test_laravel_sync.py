import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base, SessionLocal, get_db, engine
from main import app
from models import User


class LaravelSyncTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        self.db = SessionLocal()
        self.user = User(name="Old Name", age=30, note="old")
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

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=engine)

    def test_sync_from_laravel_accepts_json_payload(self):
        response = self.client.post(
            "/register/sync-from-laravel",
            json={
                "user_id": self.user.id,
                "name": "New Name",
                "date_of_birth": "1990-05-10",
                "note": "work",
                "ai_notes": "Updated notes",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(self.user)
        self.assertEqual(self.user.name, "New Name")
        self.assertEqual(self.user.date_of_birth, date(1990, 5, 10))
        self.assertEqual(self.user.note, "work")
        self.assertEqual(self.user.ai_notes, "Updated notes")

    def test_put_update_user_accepts_json_payload(self):
        response = self.client.put(
            f"/register/user/{self.user.id}",
            json={
                "name": "Updated Name Via PUT",
                "date_of_birth": "1988-12-25",
                "note": "resign",
                "ai_notes": "New description",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(self.user)
        self.assertEqual(self.user.name, "Updated Name Via PUT")
        self.assertEqual(self.user.date_of_birth, date(1988, 12, 25))
        self.assertEqual(self.user.note, "resign")
        self.assertEqual(self.user.ai_notes, "New description")


if __name__ == "__main__":
    unittest.main()
