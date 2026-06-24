from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import cv2

from database import create_tables
from routers.registration import router as registration_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()                         # create all tables on startup
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        print("Warning: Cannot open webcam.")
    app.state.camera = camera
    yield
    app.state.camera.release()


app = FastAPI(title="CamScan – Face Recognition API", lifespan=lifespan)
app.include_router(registration_router)


# ── Basic Haar-cascade live stream ───────────────────────────────────────────

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def generate_frames(camera: cv2.VideoCapture):
    while True:
        ok, frame = camera.read()
        if not ok:
            break
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
        for (x, y, w, h) in faces:
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, "Face", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        ret, buf = cv2.imencode(".jpg", frame)
        if ret:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"


@app.get("/")
def home():
    return {"status": "running", "message": "CamScan Face Recognition API"}


@app.get("/video_feed")
def video_feed(request: Request):
    return StreamingResponse(
        generate_frames(request.app.state.camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )