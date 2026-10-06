"""
camera_control.py
─────────────────
IP-aware camera access manager shared by the REST camera API and the
``/ws/camera`` WebSocket endpoint.

Only one browser client may hold the camera at a time (tracked by client IP).
A stopped IP is blocked until it explicitly clears the stop. A single active
CCTV ``stream_url`` (RTSP / HTTP-MJPEG / empty = local webcam) is also tracked
so ``/video_feed`` can honour the source the UI requested.
"""

import threading
from typing import Dict, Optional

_lock = threading.Lock()
_active: Dict[str, str] = {}   # client_ip -> stream_source ("" = local webcam)
_stopped = set()               # client IPs that stopped the camera


def client_ip(request) -> str:
    """Best-effort client IP, honouring X-Forwarded-For when present."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return "unknown"


def ws_client_ip(websocket) -> str:
    """Client IP for a WebSocket connection."""
    forwarded = websocket.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if websocket.client:
        return websocket.client.host
    return "unknown"


def _snapshot(ip: Optional[str] = None) -> dict:
    is_active = (ip in _active) if ip is not None else bool(_active)
    is_stopped = (ip in _stopped) if ip is not None else False
    allowed = (not _active or is_active) and not is_stopped
    return {
        "ip":            ip or "",
        "is_allowed":    allowed,
        "is_active":     is_active,
        "is_stopped":    is_stopped,
        "active_ips":    list(_active.keys()),
        "stopped_ips":   sorted(_stopped),
        "stream_source": _active.get(ip, "") if is_active else "",
    }


def status(ip: str) -> dict:
    with _lock:
        snap = _snapshot(ip)
        snap["success"] = True
        snap["message"] = (
            "Camera available." if snap["is_allowed"]
            else "Camera unavailable for your IP."
        )
        return snap


def start(ip: str, source: Optional[str] = None) -> dict:
    with _lock:
        if ip in _stopped:
            return {**_snapshot(ip), "success": False,
                    "message": "Camera was stopped from this IP. Use clear-stop to restore access."}
        if _active and ip not in _active:
            holder = next(iter(_active))
            return {**_snapshot(ip), "success": False,
                    "message": f"Camera is already in use by {holder}."}
        _active[ip] = source or ""
        return {**_snapshot(ip), "success": True, "message": "Camera started."}


def stop(ip: str) -> dict:
    """Explicit stop — the IP is blocked until clear-stop is called."""
    with _lock:
        if ip in _active:
            del _active[ip]
            _stopped.add(ip)
            return {**_snapshot(ip), "success": True, "message": "Camera stopped."}
        return {**_snapshot(ip), "success": False,
                "message": "Camera is not active for this IP."}


def release(ip: str) -> dict:
    """Implicit release (WS disconnect) — frees the camera without blocking the IP."""
    with _lock:
        if ip in _active:
            del _active[ip]
        return {**_snapshot(ip), "success": True, "message": "Camera released."}


def clear_stop(ip: str) -> dict:
    with _lock:
        _stopped.discard(ip)
        return {**_snapshot(ip), "success": True, "message": "Camera access restored."}


def snapshot() -> dict:
    with _lock:
        return _snapshot()


def active_ips() -> dict:
    with _lock:
        return {"active_ips": list(_active.keys()),
                "stopped_ips": sorted(_stopped)}


def current_source() -> Optional[str]:
    """First non-empty active source, or None (local webcam)."""
    with _lock:
        for source in _active.values():
            if source:
                return source
        return None