"""
capture_service.py
──────────────────
Manages Webcam #1 (registration camera) with:
  - 3-2-1 voice countdown via pyttsx3 (offline TTS)
  - Face capture at the end of countdown
  - Returns raw JPEG bytes of the captured frame
"""

import io
import math
import struct
import threading
import time
import wave
import cv2
import numpy as np
from typing import Optional
from fastapi import HTTPException

# ── Camera singleton (Webcam #1) ─────────────────────────────────────────────

_camera: Optional[cv2.VideoCapture] = None
_lock = threading.Lock()


def get_camera() -> cv2.VideoCapture:
    global _camera
    if _camera is None or not _camera.isOpened():
        _camera = cv2.VideoCapture(0)   # Webcam #1 — index 0
        if not _camera.isOpened():
            raise HTTPException(status_code=503, detail="Registration camera (Webcam #1) not available.")
        # Warm up — discard first few frames
        for _ in range(5):
            _camera.read()
    return _camera


def release_camera():
    global _camera
    if _camera:
        _camera.release()
        _camera = None


# ── TTS voice countdown ───────────────────────────────────────────────────────

def _speak(text: str):
    """Speak text using pyttsx3 (runs offline, no API needed)."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
        engine.setProperty("volume", 1.0)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"[TTS] Warning: {e}")   # non-fatal — continue without voice


def _read_fresh_frame(camera: cv2.VideoCapture) -> np.ndarray:
    # Flush stale frames then grab a fresh one
    for _ in range(3):
        camera.grab()

    ok, frame = camera.read()
    if not ok or frame is None:
        raise HTTPException(status_code=503, detail="Failed to capture frame from camera.")

    return frame


def _countdown_and_capture(camera: cv2.VideoCapture) -> np.ndarray:
    """Say 3 → 2 → 1 → Smile!, then capture a fresh frame."""
    for number in ["3", "2", "1"]:
        _speak(number)
        time.sleep(1.0)

    _speak("Smile!")
    return _read_fresh_frame(camera)


def _encode_jpeg(frame: np.ndarray, quality: int = 95) -> bytes:
    ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to encode captured frame.")
    return buffer.tobytes()


# ── Public API ────────────────────────────────────────────────────────────────

def capture_with_countdown() -> bytes:
    """
    Trigger 3-2-1 voice countdown on Webcam #1 and return JPEG bytes.
    Thread-safe — only one capture at a time.
    """
    with _lock:
        camera = get_camera()
        frame  = _countdown_and_capture(camera)

    return _encode_jpeg(frame)


def capture_frame() -> bytes:
    """Capture one fresh frame without server-side sound."""
    with _lock:
        camera = get_camera()
        frame = _read_fresh_frame(camera)

    return _encode_jpeg(frame)


def read_camera_frame() -> np.ndarray:
    """Read one frame from the shared registration camera."""
    with _lock:
        camera = get_camera()
        ok, frame = camera.read()
    if not ok or frame is None:
        raise HTTPException(status_code=503, detail="Cannot read from camera.")
    return frame


def capture_preview_frame() -> bytes:
    """Return a single live frame (no countdown) — used for camera preview check."""
    return _encode_jpeg(read_camera_frame(), quality=90)


def build_countdown_audio() -> bytes:
    """
    Build a browser-playable WAV countdown cue.
    The browser plays this, then calls capture_frame() so sound comes from the web UI.
    """
    sample_rate = 44100
    amplitude = 16000
    parts = [
        (660, 0.22), (0, 0.78),
        (660, 0.22), (0, 0.78),
        (660, 0.22), (0, 0.78),
        (880, 0.45), (0, 0.15),
    ]

    pcm = bytearray()
    for frequency, seconds in parts:
        samples = int(sample_rate * seconds)
        for i in range(samples):
            if frequency:
                value = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
            else:
                value = 0
            pcm.extend(struct.pack("<h", value))

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(pcm))
    return output.getvalue()
