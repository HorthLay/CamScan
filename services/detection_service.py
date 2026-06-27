"""
detection_service.py
────────────────────
Handles all detection logic:
  - Saving detection logs to DB
  - Saving snapshot images
  - Loading detection history
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from sqlalchemy.orm import Session
from models import Detection, User

SNAPSHOT_DIR = Path("snapshots")
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def save_snapshot(frame_bytes: bytes, camera_id: str) -> str:
    filename = f"{camera_id}_{uuid.uuid4().hex}.jpg"
    path     = SNAPSHOT_DIR / filename
    path.write_bytes(frame_bytes)
    return str(path)


def log_detection(
    db:            Session,
    user_id:       Optional[int],
    confidence:    Optional[float],
    camera_id:     str,
    camera_name:   str,
    position:      Optional[str],
    snapshot_path: Optional[str],
) -> Detection:
    record = Detection(
        user_id       = user_id,
        confidence    = str(round(confidence, 4)) if confidence else None,
        camera_id     = camera_id,
        camera_name   = camera_name,
        position      = position,
        snapshot_path = snapshot_path,
        detected_at   = datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_recent_detections(db: Session, limit: int = 50) -> List[dict]:
    rows = (
        db.query(Detection, User)
        .outerjoin(User, Detection.user_id == User.id)
        .order_by(Detection.detected_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":            det.id,
            "user_id":       det.user_id,
            "name":          user.name if user else "Unknown",
            "position":      det.position,
            "confidence":    det.confidence,
            "camera_id":     det.camera_id,
            "camera_name":   det.camera_name,
            "snapshot_path": det.snapshot_path,
            "detected_at":   det.detected_at.isoformat() if det.detected_at else None,
        }
        for det, user in rows
    ]


def get_detections_by_user(db: Session, user_id: int) -> List[dict]:
    rows = db.query(Detection).filter(Detection.user_id == user_id).order_by(Detection.detected_at.desc()).all()
    return [
        {
            "id":            d.id,
            "confidence":    d.confidence,
            "camera_id":     d.camera_id,
            "camera_name":   d.camera_name,
            "snapshot_path": d.snapshot_path,
            "detected_at":   d.detected_at.isoformat() if d.detected_at else None,
        }
        for d in rows
    ]