"""
capture_service.py
──────────────────
Manages Webcam #1 (registration camera) with:
  - 3-2-1 voice countdown via pyttsx3 (offline TTS)
  - Face capture at the end of countdown
  - Returns raw JPEG bytes of the captured frame
  - IP-based camera access control
"""

import io
import math
import struct
import threading
import time
import wave
import cv2
import numpy as np
from typing import Optional, Set
from fastapi import HTTPException

# ── Camera singleton (Webcam #1) ─────────────────────────────────────────────

_camera: Optional[cv2.VideoCapture] = None
_lock = threading.Lock()
_active_streams = 0
_camera_lock = threading.Lock()
# Track which IP addresses currently have the camera open
_active_ips: Set[str] = set()
# Track which IP has explicitly stopped the camera (cannot reopen while others are using it)
_stopped_ips: Set[str] = set()
# Track the current stream source (None = local webcam 0, string = URL)
_current_stream_source: Optional[str] = None


def get_camera(stream_source: Optional[str] = None) -> cv2.VideoCapture:
    global _camera, _current_stream_source
    source = stream_source or _current_stream_source
    print(f"[CAMERA] get_camera() called, source={source}")
    if _camera is None or not _camera.isOpened():
        cap_target = source if source else 0
        print(f"[CAMERA] Initializing cv2.VideoCapture({cap_target})")
        _camera = cv2.VideoCapture(cap_target)
        if not _camera.isOpened():
            _camera = None
            print(f"[CAMERA] ERROR: Camera not available for source {cap_target}")
            raise HTTPException(status_code=503, detail=f"Camera source not available: {cap_target}")
        _current_stream_source = source
        # Warm up — discard first few frames
        for _ in range(5):
            _camera.read()
    return _camera


def release_camera(force: bool = False):
    """Release the camera. Safe to call even if camera is already released."""
    global _camera, _current_stream_source
    print(f"[CAMERA] release_camera(force={force}) called, active_streams={_active_streams}, active_ips={len(_active_ips)}")
    
    with _lock:
        if _active_streams > 0 and not force:
            print("[CAMERA] Camera NOT released because active_streams > 0")
            return
        # Also check if any IPs are still using the camera
        if len(_active_ips) > 0 and not force:
            print(f"[CAMERA] Camera NOT released because IPs {_active_ips} are still using it")
            return
        try:
            if _camera is not None:
                print(f"[CAMERA] Releasing camera (source={_current_stream_source})")
                _camera.release()
                print("[CAMERA] Camera successfully released")
        except Exception as e:
            print(f"[CAMERA] Warning during release: {e}")
        finally:
            _camera = None
            _current_stream_source = None


def start_camera_for_ip(ip: str, stream_url: Optional[str] = None) -> bool:
    """
    Start camera access for a specific IP address.
    If stream_url is provided, connect to that RTSP/HTTP stream instead of local webcam.
    Returns True if camera was started, False if IP is blocked.
    """
    global _camera, _active_ips, _stopped_ips, _current_stream_source
    
    # Safety: handle None/empty IP
    if not ip:
        ip = "unknown"
    
    print(f"[CAMERA] start_camera_for_ip({ip}, stream_url={stream_url}) called")
    
    with _camera_lock:
        # Check if this IP has explicitly stopped the camera
        if ip in _stopped_ips:
            # Check if other IPs are currently using the camera
            other_ips_active = len(_active_ips - {ip}) > 0
            if other_ips_active:
                print(f"[CAMERA] IP {ip} is blocked from reopening camera (stopped_ips) while other IPs are using it")
                return False
            else:
                # No other IPs are using it, allow this IP to reuse
                _stopped_ips.discard(ip)
                print(f"[CAMERA] IP {ip} allowed to reuse camera (no other IPs active)")
        
        # If a new stream_url is provided and differs from current source, switch
        if stream_url and _current_stream_source != stream_url:
            # Release existing camera so we can open the new source
            with _lock:
                try:
                    if _camera is not None and _camera.isOpened():
                        print(f"[CAMERA] Switching source from {_current_stream_source} to {stream_url}")
                        _camera.release()
                except Exception as e:
                    print(f"[CAMERA] Warning during source switch release: {e}")
                _camera = None
                _current_stream_source = None
        
        # Add IP to active IPs
        _active_ips.add(ip)
        print(f"[CAMERA] IP {ip} added to active_ips: {_active_ips}")
        
        # Ensure camera is initialized
        with _lock:
            if _camera is None or not _camera.isOpened():
                cap_target = stream_url if stream_url else 0
                print(f"[CAMERA] Initializing camera for IP {ip} with source {cap_target}")
                try:
                    _camera = cv2.VideoCapture(cap_target)
                    if not _camera.isOpened():
                        _active_ips.discard(ip)
                        _camera = None
                        print(f"[CAMERA] ERROR: Camera source not available: {cap_target}")
                        raise RuntimeError(f"Camera source not available: {cap_target}")
                    _current_stream_source = stream_url
                    # Warm up
                    for _ in range(5):
                        _camera.read()
                    print(f"[CAMERA] Camera initialized for IP {ip} from source {cap_target}")
                except RuntimeError:
                    raise  # Re-raise our own error
                except Exception as e:
                    _active_ips.discard(ip)
                    _camera = None
                    print(f"[CAMERA] ERROR initializing camera: {e}")
                    raise RuntimeError(f"Camera initialization failed: {e}")
        
        return True


def stop_camera_for_ip(ip: str) -> bool:
    """
    Stop camera access for a specific IP address.
    Returns True if camera was stopped for this IP.
    """
    global _active_ips, _stopped_ips
    
    # Safety: handle None/empty IP
    if not ip:
        ip = "unknown"
    
    print(f"[CAMERA] stop_camera_for_ip({ip}) called")
    
    with _camera_lock:
        # Remove IP from active IPs
        _active_ips.discard(ip)
        print(f"[CAMERA] IP {ip} removed from active_ips: {_active_ips}")
        
        # Add IP to stopped IPs
        _stopped_ips.add(ip)
        print(f"[CAMERA] IP {ip} added to stopped_ips: {_stopped_ips}")
        
        # Release camera if no IPs are using it
        if len(_active_ips) == 0:
            release_camera(force=True)
        
        return True


def is_ip_allowed(ip: str) -> bool:
    """
    Check if an IP is allowed to use the camera.
    """
    with _camera_lock:
        # If IP is in stopped_ips, check if others are using it
        if ip in _stopped_ips:
            return len(_active_ips - {ip}) == 0
        return True


def get_active_ips() -> Set[str]:
    """Get the set of currently active IPs."""
    with _camera_lock:
        return _active_ips.copy()


def get_stopped_ips() -> Set[str]:
    """Get the set of stopped IPs."""
    with _camera_lock:
        return _stopped_ips.copy()


def get_current_stream_source() -> Optional[str]:
    """Get the current camera stream source (None = local webcam)."""
    with _camera_lock:
        return _current_stream_source


def clear_stopped_ip(ip: str):
    """Clear a stopped IP (allow it to use camera again)."""
    global _stopped_ips
    with _camera_lock:
        _stopped_ips.discard(ip)
        print(f"[CAMERA] IP {ip} cleared from stopped_ips")


def start_stream():
    global _active_streams
    with _lock:
        _active_streams += 1
        print(f"[CAMERA] start_stream() called, active_streams={_active_streams}")


def stop_stream():
    global _active_streams
    with _lock:
        _active_streams = max(0, _active_streams - 1)
        print(f"[CAMERA] stop_stream() called, active_streams={_active_streams}")
    release_camera()
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
