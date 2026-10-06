"""
routers/camera.py
─────────────────
REST camera-control endpoints consumed by the Laravel dashboard
(CameraController + camera control/websocket views).

These mirror the same IP-aware state that the ``/ws/camera`` socket uses, so
REST and WebSocket clients observe one consistent camera owner.
"""

from typing import Optional
from fastapi import APIRouter, Form, Request

from services import camera_control

router = APIRouter(tags=["Camera"])


@router.get("/register/camera/status", summary="Camera status for the requesting IP")
def camera_status(request: Request):
    return camera_control.status(camera_control.client_ip(request))


@router.post("/register/camera/start", summary="Start camera for the requesting IP")
def camera_start(
    request: Request,
    stream_url: Optional[str] = Form(None),
):
    return camera_control.start(camera_control.client_ip(request), stream_url)


@router.post("/register/camera/stop", summary="Stop camera for the requesting IP")
def camera_stop(request: Request):
    return camera_control.stop(camera_control.client_ip(request))


@router.post("/register/camera/clear-stop", summary="Clear blocked status for the requesting IP")
def camera_clear_stop(request: Request):
    return camera_control.clear_stop(camera_control.client_ip(request))


@router.get("/register/camera/active-ips", summary="List active and stopped IPs")
def camera_active_ips():
    return camera_control.active_ips()


@router.get("/camera/source", summary="Currently active stream source (used to resume sessions)")
def camera_source():
    snap = camera_control.snapshot()
    return {
        "is_active":     snap["is_active"],
        "stream_source": camera_control.current_source() or "",
    }