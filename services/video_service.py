"""
video_service.py
────────────────
Handles video recording and DB logging.
Writes frames to an MP4 file, then saves the path to the videos table.
"""


import uuid
import cv2
from datetime import datetime
from pathlib import Path
 
from sqlalchemy.orm import Session
from models import Video
 
VIDEO_DIR = Path("videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
 
# Active recordings: camera_id → {"writer": VideoWriter, "path": str, "started_at": datetime}
_active_recordings: dict = {}
 
 
# ── Start / Stop recording ────────────────────────────────────────────────────
 
def start_recording(camera_id: str, frame_width: int, frame_height: int, fps: float = 20.0) -> str:
    """Start writing frames to a new MP4 file. Returns the file path."""
    if camera_id in _active_recordings:
        return _active_recordings[camera_id]["path"]   # already recording
 
    filename = f"{camera_id}_{uuid.uuid4().hex}.mp4"
    path     = str(VIDEO_DIR / filename)
 
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (frame_width, frame_height))
 
    _active_recordings[camera_id] = {
        "writer":     writer,
        "path":       path,
        "started_at": datetime.utcnow(),
    }
    return path
 
 
def write_frame(camera_id: str, frame) -> bool:
    """Append a frame to the active recording. Returns False if not recording."""
    rec = _active_recordings.get(camera_id)
    if not rec:
        return False
    rec["writer"].write(frame)
    return True
 
 
def stop_recording(camera_id: str, db: Session, user_id: int | None = None) -> Video | None:
    """
    Stop recording, flush the file, and log it to the videos table.
    Returns the Video DB record.
    """
    rec = _active_recordings.pop(camera_id, None)
    if not rec:
        return None
 
    rec["writer"].release()
 
    record = Video(
        user_id    = user_id,
        video_path = rec["path"],
        created_at = datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
 
 
# ── Queries for Laravel dashboard ─────────────────────────────────────────────
 
def get_all_videos(db: Session) -> list[dict]:
    rows = db.query(Video).order_by(Video.created_at.desc()).all()
    return [
        {
            "id":         v.id,
            "user_id":    v.user_id,
            "video_path": v.video_path,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in rows
    ]
 