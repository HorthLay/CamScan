"""
routers/detection.py
────────────────────
Detection endpoints consumed by the Laravel dashboard's DetectionController:

  POST /detect                  — analyze a base64 image / stream frame
  POST /detect/stream/start     — begin live detection on a CCTV stream
  POST /detect/stream/stop      — end live detection
  GET  /detect/status           — active detection state
  GET  /detect/results          — recent detections

These back the same IP-aware camera model as ``/ws/camera`` and the REST
camera API, so the CCTV panel and the WebSocket view share one source of truth.
"""

import base64
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from services import camera_control
from services.embedding_cache import get_embeddings
from services.face_service import decode_image, find_best_match, generate_embedding
from services.user_service import load_all_embeddings

router = APIRouter(tags=["Detection"])

_recent_results: deque = deque(maxlen=100)


def _serialize_match(match: dict) -> dict:
    return {
        "id":            match.get("user_id"),
        "name":          match.get("name"),
        "age":           match.get("age"),
        "date_of_birth": match.get("date_of_birth"),
        "gender":        match.get("gender"),
        "position":      match.get("position"),
        "face_image":    match.get("face_image"),
        "image_user":    match.get("image_user"),
        "ai_notes":      match.get("ai_notes"),
        "note":          match.get("note"),
        "confidence":    match.get("confidence"),
    }


def _analyze_frame(img_bgr, db: Session) -> dict:
    faces = []
    try:
        from services.uniface_service import detect_faces
        faces = [
            {"bbox": det["bbox"], "confidence": det["confidence"]}
            for det in detect_faces(img_bgr)
        ]
    except Exception:
        pass

    probe = generate_embedding(img_bgr)
    match = find_best_match(
        probe,
        get_embeddings(lambda: load_all_embeddings(db)),
        threshold=0.55,
    )

    result = {
        "success": True,
        "num_faces": len(faces),
        "faces": faces,
        "matched": match is not None,
    }

    if match:
        result["match"] = _serialize_match(match)
        result["message"] = f"Matched {match.get('name')}."
        _recent_results.appendleft({
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "name":        match.get("name"),
            "user_id":     match.get("user_id"),
            "confidence":  match.get("confidence"),
            "position":    match.get("position"),
        })
    else:
        result["message"] = "No matching user found."

    return result


@router.post("/detect", summary="Detect faces in a submitted image/stream frame")
async def detect(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    image_data = payload.get("image_data")
    stream_url = payload.get("stream_url")

    if not image_data and not stream_url:
        raise HTTPException(status_code=400, detail="Provide image_data or stream_url.")

    if image_data:
        try:
            raw = base64.b64decode(image_data)
        except Exception:
            raise HTTPException(status_code=400, detail="image_data must be base64.")
        return _analyze_frame(decode_image(raw), db)

    # stream_url given → sniff one frame from the CCTV source and analyze it.
    import cv2
    import platform
    _is_win = platform.system() == "Windows"
    cap = cv2.VideoCapture(stream_url, cv2.CAP_DSHOW) if _is_win else cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        raise HTTPException(status_code=503, detail=f"Cannot open stream: {stream_url}")
    try:
        for _ in range(3):
            cap.grab()
        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok or frame is None:
        raise HTTPException(status_code=503, detail="Could not read frame from stream.")

    return _analyze_frame(frame, db)


@router.post("/detect/stream/start", summary="Start live detection on a CCTV stream")
async def start_stream(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    stream_url = payload.get("stream_url")
    if not stream_url:
        raise HTTPException(status_code=400, detail="stream_url is required.")

    ip = camera_control.client_ip(request)
    result = camera_control.start(ip, stream_url)
    if not result["success"]:
        raise HTTPException(status_code=403, detail=result["message"])

    return {
        "success":      True,
        "is_detecting": result["is_active"],
        "stream_url":   stream_url,
        "active_ips":   result["active_ips"],
        "message":      "Stream detection started.",
    }


@router.post("/detect/stream/stop", summary="Stop live detection")
async def stop_stream(request: Request):
    ip = camera_control.client_ip(request)
    result = camera_control.stop(ip)
    return {
        "success":      result["success"],
        "is_detecting": result["is_active"],
        "active_ips":   result["active_ips"],
        "message":      result["message"],
    }


@router.get("/detect/status", summary="Detection status for the requesting IP")
def detect_status(request: Request):
    snap = camera_control.status(camera_control.client_ip(request))
    return {
        "success":      True,
        "is_detecting": snap["is_active"],
        "is_allowed":   snap["is_allowed"],
        "is_stopped":   snap["is_stopped"],
        "active_ips":   snap["active_ips"],
        "stopped_ips":  snap["stopped_ips"],
        "stream_source": camera_control.current_source() or "",
    }


@router.get("/detect/results", summary="Recent detection results")
def detect_results(limit: int = 20):
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    return {"success": True, "results": list(_recent_results)[:limit]}