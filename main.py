from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2

from database import create_tables
from routers.registration import router as registration_router
from services.capture_service import (
    get_camera, read_camera_frame, release_camera, 
    start_stream, stop_stream,
    start_camera_for_ip, stop_camera_for_ip,
    is_ip_allowed, get_active_ips, get_stopped_ips, clear_stopped_ip
)
from camera_websockets import websocket_camera_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()                         # create all tables on startup
    yield
    release_camera(force=True)


from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CamScan – Face Recognition API", lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
# Allow both HTTP and WebSocket origins
cors_origins.extend(["http://localhost:8000", "http://127.0.0.1:8000", "ws://localhost:8001", "ws://127.0.0.1:8001"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,  # Required for WebSockets with cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(registration_router)

# WebSocket endpoint for real-time camera control
@app.websocket("/ws/camera")
async def websocket_camera(websocket: WebSocket):
    await websocket_camera_endpoint(websocket)


# ── Basic Haar-cascade live stream ───────────────────────────────────────────

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def generate_frames():
    start_stream()
    try:
        while True:
            try:
                frame = read_camera_frame()
            except Exception:
                break
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(frame, "Face", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            ret, buf = cv2.imencode(".jpg", frame)
            if ret:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
    finally:
        stop_stream()


@app.get("/")
def home():
    return {"status": "running", "message": "CamScan Face Recognition API"}


from fastapi import Request

@app.get("/video_feed")
def video_feed(request: Request):
    """
    Stream camera feed. Checks if requesting IP is allowed to use the camera.
    """
    client_ip = request.client.host
    
    # Check if IP is allowed to use camera
    if not is_ip_allowed(client_ip):
        raise HTTPException(
            status_code=403,
            detail=f"IP {client_ip} is not allowed to access camera (another IP is using it)"
        )
    
    # Start camera for this IP if not already active
    start_camera_for_ip(client_ip)
    
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
