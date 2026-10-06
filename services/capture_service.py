"""
capture_service.py
──────────────────
Manages Webcam #1 (registration camera) with:
  - 3-2-1 voice countdown via pyttsx3 (offline TTS)
  - Face capture at the end of countdown
  - Returns raw JPEG bytes of the captured frame
"""

import io
import logging
import platform
import threading
import time
import wave
import cv2
import numpy as np
from typing import Optional
from fastapi import HTTPException

logger = logging.getLogger("camscan.capture")

# ── Platform-aware camera backend ────────────────────────────────────────────
# Windows MSMF (default) frequently hangs on VideoCapture(0). DirectShow is
# reliable and fast. On Linux/macOS the default (V4L2/AVFoundation) is fine.

_IS_WINDOWS = platform.system() == "Windows"
_CAMERA_BACKEND = cv2.CAP_DSHOW if _IS_WINDOWS else cv2.CAP_ANY

# ── Camera singleton (Webcam #1) ─────────────────────────────────────────────

_camera: Optional[cv2.VideoCapture] = None
_lock = threading.Lock()

CAMERA_WIDTH  = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS    = 30


def get_camera() -> cv2.VideoCapture:
    global _camera
    if _camera is None or not _camera.isOpened():
        logger.info("Opening webcam 0 with backend %s ...",
                     "DSHOW" if _IS_WINDOWS else "ANY")
        _camera = cv2.VideoCapture(0, _CAMERA_BACKEND)
        if not _camera.isOpened():
            logger.error("Failed to open registration camera (Webcam #1).")
            raise HTTPException(status_code=503, detail="Registration camera (Webcam #1) not available.")
        _camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        _camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        _camera.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        # Warm up — discard first few frames
        for _ in range(3):
            _camera.read()
        logger.info("Webcam 0 ready (%dx%d).", CAMERA_WIDTH, CAMERA_HEIGHT)
    return _camera


def release_camera():
    global _camera
    if _camera:
        _camera.release()
        _camera = None


def camera_available() -> bool:
    """Cheap check: has the shared camera been opened and is it still alive?"""
    return _camera is not None and _camera.isOpened()


# ── TTS voice countdown ───────────────────────────────────────────────────────

_tts_engine = None
_tts_lock = threading.Lock()


def _get_tts_engine():
    """Create the pyttsx3 engine once and reuse it (engine init is expensive)."""
    global _tts_engine
    if _tts_engine is None:
        import pyttsx3
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", 160)
        _tts_engine.setProperty("volume", 1.0)
    return _tts_engine


def _speak(text: str):
    """Speak text using pyttsx3 (runs offline, no API needed)."""
    try:
        with _tts_lock:
            engine = _get_tts_engine()
            engine.say(text)
            engine.runAndWait()
    except Exception as e:
        print(f"[TTS] Warning: {e}")   # non-fatal — continue without voice


def _read_fresh_frame(camera: cv2.VideoCapture) -> np.ndarray:
    # Flush stale frames then grab a fresh one
    for _ in range(3):
        camera.grab()

    ok, frame = camera.read()
    if not ok or frame is None:
        release_camera()
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

    t = np.arange(int(sample_rate * sum(seconds for _, seconds in parts))) / sample_rate
    signal = np.zeros_like(t)
    cursor = 0
    for frequency, seconds in parts:
        start = cursor
        end = cursor + int(sample_rate * seconds)
        if frequency:
            signal[start:end] = amplitude * np.sin(
                2 * np.pi * frequency * np.arange(start, end) / sample_rate
            )
        cursor = end

    pcm = signal.astype(np.int16).tobytes()

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
