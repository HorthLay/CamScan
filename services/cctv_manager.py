"""
cctv_manager.py
───────────────
Multi-CCTV camera manager with:
  - Named camera registry (add/remove/list cameras)
  - Per-camera live detection threads
  - Detection event logging with daily stats
  - Background frame-grabbing for each stream

Env knobs:
  CCTV_DETECT_INTERVAL   seconds between detect passes, default 3
  CCTV_FRAME_TIMEOUT     seconds to wait for a frame, default 10
"""

import logging
import os
import platform
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, date
from typing import Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger("camscan.cctv")

_IS_WINDOWS = platform.system() == "Windows"
_CAMERA_BACKEND = cv2.CAP_DSHOW if _IS_WINDOWS else cv2.CAP_ANY

DETECT_INTERVAL = float(os.getenv("CCTV_DETECT_INTERVAL", "3"))
FRAME_TIMEOUT = float(os.getenv("CCTV_FRAME_TIMEOUT", "10"))


# ── Camera registry ─────────────────────────────────────────────────────────

class CCTVCamera:
    """Represents a registered CCTV/webcam source."""

    def __init__(self, camera_id: str, name: str, source: str, location: str = ""):
        self.camera_id = camera_id
        self.name = name
        self.source = source        # RTSP URL, HTTP URL, or "0", "1" etc for local webcam
        self.location = location
        self.added_at = datetime.utcnow()
        self.is_active = False
        self.last_frame_at: Optional[datetime] = None
        self.last_detection_at: Optional[datetime] = None
        self.error: Optional[str] = None


_cameras: Dict[str, CCTVCamera] = {}
_cameras_lock = threading.Lock()

# Detection workers
_workers: Dict[str, threading.Thread] = {}
_worker_stop: Dict[str, threading.Event] = {}

# Detection results (in-memory, per camera, most recent first)
_detection_log: List[dict] = []
_detection_lock = threading.Lock()
_MAX_LOG = 500

# Daily counts: { "camera_id": { "2026-09-24": { "user_id": count, ... } } }
_daily_counts: Dict[str, Dict[str, Dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
_daily_counts_lock = threading.Lock()

# Latest frame per camera (for preview)
_latest_frames: Dict[str, bytes] = {}
_frames_lock = threading.Lock()


def _parse_source(source: str):
    """Parse source string: numeric → int index, otherwise keep as URL."""
    try:
        return int(source)
    except ValueError:
        return source


def add_camera(name: str, source: str, location: str = "") -> CCTVCamera:
    """Register a new CCTV camera."""
    camera_id = uuid.uuid4().hex[:8]
    cam = CCTVCamera(camera_id=camera_id, name=name, source=source, location=location)
    with _cameras_lock:
        _cameras[camera_id] = cam
    logger.info("Added camera %s: %s (%s)", camera_id, name, source)
    return cam


def remove_camera(camera_id: str) -> bool:
    """Remove a registered camera and stop its worker."""
    stop_detection(camera_id)
    with _cameras_lock:
        cam = _cameras.pop(camera_id, None)
    if cam:
        logger.info("Removed camera %s: %s", camera_id, cam.name)
        return True
    return False


def list_cameras() -> List[dict]:
    """List all registered cameras with their status."""
    with _cameras_lock:
        cams = list(_cameras.values())
    result = []
    for c in cams:
        result.append({
            "camera_id": c.camera_id,
            "name": c.name,
            "source": c.source,
            "location": c.location,
            "is_active": c.is_active,
            "last_frame_at": c.last_frame_at.isoformat() if c.last_frame_at else None,
            "last_detection_at": c.last_detection_at.isoformat() if c.last_detection_at else None,
            "error": c.error,
            "added_at": c.added_at.isoformat(),
        })
    return result


def get_camera_info(camera_id: str) -> Optional[dict]:
    """Get info for a single camera."""
    with _cameras_lock:
        cam = _cameras.get(camera_id)
    if not cam:
        return None
    return {
        "camera_id": cam.camera_id,
        "name": cam.name,
        "source": cam.source,
        "location": cam.location,
        "is_active": cam.is_active,
        "last_frame_at": cam.last_frame_at.isoformat() if cam.last_frame_at else None,
        "last_detection_at": cam.last_detection_at.isoformat() if cam.last_detection_at else None,
        "error": cam.error,
        "added_at": cam.added_at.isoformat(),
    }


# ── Detection worker ────────────────────────────────────────────────────────

def _detection_worker(camera_id: str, stop_event: threading.Event):
    """Background thread: grab frames, run face detection + recognition."""
    with _cameras_lock:
        cam = _cameras.get(camera_id)
    if not cam:
        return

    source = _parse_source(cam.source)
    logger.info("Detection worker starting for camera %s (source=%s)", camera_id, source)

    if isinstance(source, int):
        cap = cv2.VideoCapture(source, _CAMERA_BACKEND)
    else:
        if _IS_WINDOWS:
            cap = cv2.VideoCapture(source, _CAMERA_BACKEND)
        else:
            cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        cam.error = f"Cannot open source: {cam.source}"
        cam.is_active = False
        logger.error("Cannot open camera %s: %s", camera_id, cam.source)
        return

    cam.is_active = True
    cam.error = None

    # Lazy-import heavy services only when worker starts
    try:
        from services.face_service import generate_embedding, find_best_match
        from services.embedding_cache import get_embeddings
        from services.user_service import load_all_embeddings
        from database import SessionLocal
        has_recognition = True
    except Exception as exc:
        logger.warning("Recognition unavailable for camera %s: %s", camera_id, exc)
        has_recognition = False

    try:
        from services.uniface_service import detect_faces as uniface_detect
        has_uniface = True
    except Exception:
        has_uniface = False

    consecutive_fails = 0
    while not stop_event.is_set():
        try:
            # Flush stale frames
            for _ in range(2):
                cap.grab()

            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_fails += 1
                if consecutive_fails > 30:
                    cam.error = "Too many consecutive read failures"
                    break
                time.sleep(0.5)
                continue

            consecutive_fails = 0
            cam.last_frame_at = datetime.utcnow()

            # Resize for faster detection
            h, w = frame.shape[:2]
            if w > 640:
                scale = 640 / w
                frame = cv2.resize(frame, (640, int(h * scale)), interpolation=cv2.INTER_AREA)

            # Encode frame for preview
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                with _frames_lock:
                    _latest_frames[camera_id] = buf.tobytes()

            # Detect faces
            faces = []
            if has_uniface:
                try:
                    faces = uniface_detect(frame)
                except Exception:
                    pass

            if not faces:
                # No faces detected, sleep and continue
                stop_event.wait(DETECT_INTERVAL)
                continue

            # Try to recognize each face
            cam.last_detection_at = datetime.utcnow()
            today_str = date.today().isoformat()

            if has_recognition:
                try:
                    db = SessionLocal()
                    try:
                        probe = generate_embedding(frame)
                        candidates = get_embeddings(lambda: load_all_embeddings(db))
                        match = find_best_match(probe, candidates, threshold=0.55)

                        detection_entry = {
                            "id": uuid.uuid4().hex[:12],
                            "camera_id": camera_id,
                            "camera_name": cam.name,
                            "detected_at": datetime.utcnow().isoformat(),
                            "num_faces": len(faces),
                            "matched": match is not None,
                        }

                        if match:
                            detection_entry.update({
                                "user_id": match.get("user_id"),
                                "name": match.get("name"),
                                "age": match.get("age"),
                                "gender": match.get("gender"),
                                "position": match.get("position"),
                                "confidence": match.get("confidence"),
                                "face_image": match.get("face_image"),
                                "note": match.get("note"),
                            })
                            # Update daily count
                            uid = match.get("user_id", 0)
                            with _daily_counts_lock:
                                _daily_counts[camera_id][today_str][uid] += 1

                            # Also log to DB
                            from services.detection_service import log_detection, save_snapshot
                            snapshot_path = None
                            if ret:
                                snapshot_path = save_snapshot(buf.tobytes(), camera_id)
                            log_detection(
                                db=db,
                                user_id=match.get("user_id"),
                                confidence=match.get("confidence"),
                                camera_id=camera_id,
                                camera_name=cam.name,
                                position=match.get("position"),
                                snapshot_path=snapshot_path,
                            )
                        else:
                            detection_entry.update({
                                "user_id": None,
                                "name": "Unknown",
                                "confidence": 0,
                            })

                        with _detection_lock:
                            _detection_log.insert(0, detection_entry)
                            if len(_detection_log) > _MAX_LOG:
                                _detection_log[:] = _detection_log[:_MAX_LOG]

                    finally:
                        db.close()
                except Exception as exc:
                    logger.warning("Recognition error on camera %s: %s", camera_id, exc)

            stop_event.wait(DETECT_INTERVAL)

        except Exception as exc:
            logger.error("Worker error for camera %s: %s", camera_id, exc)
            stop_event.wait(DETECT_INTERVAL)

    cap.release()
    cam.is_active = False
    logger.info("Detection worker stopped for camera %s", camera_id)


def start_detection(camera_id: str) -> bool:
    """Start detection worker for a camera."""
    if camera_id in _workers and _workers[camera_id].is_alive():
        return True  # already running

    with _cameras_lock:
        if camera_id not in _cameras:
            return False

    stop_event = threading.Event()
    _worker_stop[camera_id] = stop_event

    worker = threading.Thread(
        target=_detection_worker,
        args=(camera_id, stop_event),
        daemon=True,
        name=f"cctv-{camera_id}",
    )
    _workers[camera_id] = worker
    worker.start()
    return True


def stop_detection(camera_id: str) -> bool:
    """Stop detection worker for a camera."""
    event = _worker_stop.pop(camera_id, None)
    if event:
        event.set()

    worker = _workers.pop(camera_id, None)
    if worker and worker.is_alive():
        worker.join(timeout=5)
    return True


# ── Query helpers ────────────────────────────────────────────────────────────

def get_detections(camera_id: Optional[str] = None, limit: int = 50) -> List[dict]:
    """Recent detection events, optionally filtered by camera."""
    with _detection_lock:
        if camera_id:
            items = [d for d in _detection_log if d["camera_id"] == camera_id]
        else:
            items = list(_detection_log)
    return items[:limit]


def get_daily_stats(camera_id: Optional[str] = None, day: Optional[str] = None) -> dict:
    """
    Daily detection stats.
    Returns { camera_id: { user_id: { name, count, ... }, ... } }
    """
    target_day = day or date.today().isoformat()
    result = {}

    with _daily_counts_lock:
        cam_ids = [camera_id] if camera_id else list(_daily_counts.keys())
        for cid in cam_ids:
            if cid in _daily_counts and target_day in _daily_counts[cid]:
                counts = dict(_daily_counts[cid][target_day])
                result[cid] = counts

    return {"date": target_day, "stats": result}


def get_camera_frame(camera_id: str) -> Optional[bytes]:
    """Latest JPEG frame from a camera worker."""
    with _frames_lock:
        return _latest_frames.get(camera_id)


def generate_camera_stream(camera_id: str):
    """MJPEG stream generator for a specific CCTV camera."""
    while True:
        frame = get_camera_frame(camera_id)
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.1)  # ~10 FPS for CCTV preview
