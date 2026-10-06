"""
routers/cctv.py
───────────────
Multi-CCTV camera management and detection endpoints.

  GET    /cctv/cameras                 — list all cameras
  POST   /cctv/cameras                 — add a camera
  DELETE /cctv/cameras/{camera_id}     — remove a camera
  POST   /cctv/cameras/{camera_id}/start  — start detection
  POST   /cctv/cameras/{camera_id}/stop   — stop detection
  GET    /cctv/cameras/{camera_id}/stream — MJPEG stream
  GET    /cctv/cameras/{camera_id}/frame  — single JPEG frame
  GET    /cctv/detections              — recent detection events
  GET    /cctv/stats                   — daily detection stats
  GET    /cctv/stats/{camera_id}       — daily stats for one camera
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response

from services import cctv_manager

router = APIRouter(prefix="/cctv", tags=["CCTV"])


@router.get("/cameras", summary="List all registered CCTV cameras")
def list_cameras():
    return {"success": True, "cameras": cctv_manager.list_cameras()}


@router.post("/cameras", summary="Add a new CCTV camera")
async def add_camera(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    name = payload.get("name")
    source = payload.get("source")
    location = payload.get("location", "")

    if not name or not source:
        raise HTTPException(status_code=400, detail="name and source are required")

    cam = cctv_manager.add_camera(name=name, source=source, location=location)

    # Auto-start detection if requested
    auto_start = payload.get("auto_start", False)
    if auto_start:
        cctv_manager.start_detection(cam.camera_id)

    return {
        "success": True,
        "camera_id": cam.camera_id,
        "name": cam.name,
        "source": cam.source,
        "message": f"Camera '{name}' added.",
    }


@router.delete("/cameras/{camera_id}", summary="Remove a CCTV camera")
def remove_camera(camera_id: str):
    if cctv_manager.remove_camera(camera_id):
        return {"success": True, "message": f"Camera {camera_id} removed."}
    raise HTTPException(status_code=404, detail="Camera not found.")


@router.get("/cameras/{camera_id}", summary="Get camera info")
def get_camera(camera_id: str):
    info = cctv_manager.get_camera_info(camera_id)
    if not info:
        raise HTTPException(status_code=404, detail="Camera not found.")
    return {"success": True, **info}


@router.post("/cameras/{camera_id}/start", summary="Start detection on a camera")
def start_detection(camera_id: str):
    if cctv_manager.start_detection(camera_id):
        return {"success": True, "message": "Detection started."}
    raise HTTPException(status_code=404, detail="Camera not found.")


@router.post("/cameras/{camera_id}/stop", summary="Stop detection on a camera")
def stop_detection(camera_id: str):
    cctv_manager.stop_detection(camera_id)
    return {"success": True, "message": "Detection stopped."}


@router.get("/cameras/{camera_id}/stream", summary="MJPEG stream for a camera")
def camera_stream(camera_id: str):
    info = cctv_manager.get_camera_info(camera_id)
    if not info:
        raise HTTPException(status_code=404, detail="Camera not found.")

    return StreamingResponse(
        cctv_manager.generate_camera_stream(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/cameras/{camera_id}/frame", summary="Single JPEG frame")
def camera_frame(camera_id: str):
    frame = cctv_manager.get_camera_frame(camera_id)
    if not frame:
        raise HTTPException(status_code=503, detail="No frame available.")
    return Response(content=frame, media_type="image/jpeg")


@router.get("/detections", summary="Recent detection events")
def get_detections(camera_id: Optional[str] = None, limit: int = 50):
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    return {
        "success": True,
        "detections": cctv_manager.get_detections(camera_id, limit),
    }


@router.get("/stats", summary="Daily detection stats for all cameras")
def get_stats(day: Optional[str] = None):
    return {"success": True, **cctv_manager.get_daily_stats(day=day)}


@router.get("/stats/{camera_id}", summary="Daily detection stats for one camera")
def get_camera_stats(camera_id: str, day: Optional[str] = None):
    return {"success": True, **cctv_manager.get_daily_stats(camera_id=camera_id, day=day)}
