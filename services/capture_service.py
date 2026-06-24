"""
capture_service.py
──────────────────
Manages Webcam #1 (registration camera) with:
  - 3-2-1 voice countdown via pyttsx3 (offline TTS)
  - Face capture at the end of countdown
  - Returns raw JPEG bytes of the captured frame
"""

import threading
import time
import cv2  
import numpy as np
from fastapi import HTTPException

#Camera singleton (Webcam #1) 

_camera = cv2.VideoCapture | None = None
_lock = threading.Lock()


def get_camera() -> cv2.VideoCapture:
    global _camera
    if _camera is None or not _camera.isOpened():
        _camera = cv2.VideoCapture(0)
        if not _camera.isOpened():  # Webcam #1 — index 0
              raise HTTPException(status_code=503, detail="Registration camera (Webcam #1) not available.")
        #warm up the camera few frames to allow auto-exposure and auto-focus to stabilize
        for _ in range(5):  
            _camera.read()
    return _camera
def release_camera():
    global _camera
    if _camera : 
        _camera.release()
        _camera = None


# TTS voice countdown

def _speak(text: str):
    """Speak text using pyttsx3 (runs offline, no API needed)."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)   # speed
        engine.setProperty("volume", 1.0)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"[TTS] Warning: {e}")      # non-fatal — continue without voice


def _countdown_and_capture(camera: cv2.VideoCapture) -> np.ndarray:
    """
    Say 3 → 2 → 1 → capture, returning the final frame.
    Each number is spoken then waits 1 second.
    """
    for number in ["3", "2", "1"]:
        _speak(number)
        time.sleep(1.0)
 
    _speak("Smile!")
 
    # Flush stale frames then grab fresh one
    for _ in range(3):
        camera.grab()
 
    ok, frame = camera.read()
    if not ok or frame is None:
        raise HTTPException(status_code=503, detail="Failed to capture frame from camera.")
 
    return frame


# Public Api

def capture_with_countdown() -> bytes:
    """
    Trigger 3-2-1 voice countdown on Webcam #1 and return JPEG bytes.
    Thread-safe — only one capture at a time.
    """
    with _lock:
        camera = get_camera()
        frame  = _countdown_and_capture(camera)
 
    # Encode to JPEG
    ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to encode captured frame.")
 
    return buffer.tobytes()


def capture_preview_frame() -> bytes:

    """
    Return a single live frame (no countdown) — used for camera preview check.
    """

    camera = get_camera()
    ok, frame = camera.read()
    if not ok :
        raise HTTPException(status_code=503, detail="Cannot read from camera")
    _, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes()