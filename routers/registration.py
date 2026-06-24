"""
registration.py
───────────────
Endpoints:
  POST /register/capture          → 3-2-1 countdown, capture webcam photo, analyze with Mistral
  POST /register/user             → save analyzed user + embedding to DB
  POST /register/user/{id}/face   → add extra face photo
  GET  /register/users            → list all users
  DELETE /register/user/{id}      → delete user
  GET  /register/preview          → single live frame (camera check)
"""

from fastapi import APIRouter, File, Form, UploadFile, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from services.face_service import decode_image, generate_embedding
from services.user_service import (
    create_user, get_user, get_all_users,
    delete_user, add_embedding, load_all_embeddings,
)
from services.capture_service import capture_with_countdown, capture_preview_frame
from services.mistral_service import analyze_face

router = APIRouter(prefix="/register", tags=["Registration"])

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES    = 10 * 1024 * 1024  # 10 MB


def _validate(file: UploadFile, data: bytes):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Only JPEG/PNG/WEBP accepted.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB.")


# ── STEP 1 — Trigger 3-2-1 countdown + capture + Mistral analysis ───────────

@router.post("/capture", summary="3-2-1 voice countdown → capture face → Mistral analysis")
async def capture_and_analyze():
    """
    Triggers the registration webcam (Webcam #1):
      1. Speaks '3... 2... 1... Smile!'
      2. Captures the frame
      3. Detects face + generates embedding
      4. Sends photo to Mistral Pixtral for age/position/gender analysis
      5. Returns image as base64 + all AI-suggested fields

    Frontend shows the result for user to confirm before saving.
    """
    # 1. Countdown + capture
    jpeg_bytes = capture_with_countdown()

    # 2. Detect face & generate embedding (fail fast if no face)
    img_bgr   = decode_image(jpeg_bytes)
    embedding = generate_embedding(img_bgr)   # raises 422 if no face

    # 3. Mistral vision analysis
    analysis = await analyze_face(jpeg_bytes)

    import base64
    return {
        "success":          True,
        "image_base64":     base64.b64encode(jpeg_bytes).decode(),
        "estimated_age":    analysis["estimated_age"],
        "gender":           analysis["gender"],
        "suggested_position": analysis["suggested_position"],
        "ai_notes":         analysis["notes"],
        "message":          "Face captured and analyzed. Confirm details then call POST /register/user/confirm."
    }


# ── STEP 2 — Confirm + save to database ─────────────────────────────────────

@router.post("/user/confirm", summary="Save captured + analyzed user to DB")
async def confirm_and_save(
    # User-confirmed fields (pre-filled from Mistral, editable)
    name:       str        = Form(..., description="Full name"),
    position:   str | None = Form(None),
    age:        int | None = Form(None),
    gender:     str | None = Form(None),
    ai_notes:   str | None = Form(None),

    # The captured face image (send back the one from /capture)
    face_image: UploadFile = File(..., description="Face photo from capture step"),
    image_user: UploadFile = File(None,  description="Optional profile/ID photo"),

    db: Session = Depends(get_db),
):
    """
    Called after /capture. The frontend pre-fills the form with Mistral's
    suggestions; user can edit name/age/position before submitting.
    """
    face_bytes = await face_image.read()
    _validate(face_image, face_bytes)

    profile_bytes = None
    if image_user and image_user.filename:
        profile_bytes = await image_user.read()
        _validate(image_user, profile_bytes)

    img_bgr   = decode_image(face_bytes)
    embedding = generate_embedding(img_bgr)

    user = create_user(
        db            = db,
        name          = name,
        position      = position,
        face_bytes    = face_bytes,
        profile_bytes = profile_bytes,
        embedding     = embedding,
        age           = age,
        gender        = gender,
        ai_notes      = ai_notes,
    )

    return JSONResponse(status_code=201, content={
        "success":    True,
        "user_id":    user.id,
        "name":       user.name,
        "age":        user.age,
        "gender":     user.gender,
        "position":   user.position,
        "face_image": user.face_image,
        "image_user": user.image_user,
        "ai_notes":   user.ai_notes,
        "message":    "User registered and face embedding saved.",
    })


# ── Manual upload (no countdown) ─────────────────────────────────────────────

@router.post("/user", summary="Register user by uploading photo manually")
async def register_user_manual(
    name:       str        = Form(...),
    position:   str | None = Form(None),
    age:        int | None = Form(None),
    face_image: UploadFile = File(...),
    image_user: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    face_bytes = await face_image.read()
    _validate(face_image, face_bytes)

    profile_bytes = None
    if image_user and image_user.filename:
        profile_bytes = await image_user.read()
        _validate(image_user, profile_bytes)

    # Run Mistral analysis on manually uploaded photo too
    analysis  = await analyze_face(face_bytes)
    img_bgr   = decode_image(face_bytes)
    embedding = generate_embedding(img_bgr)

    user = create_user(
        db            = db,
        name          = name,
        position      = position or analysis["suggested_position"],
        face_bytes    = face_bytes,
        profile_bytes = profile_bytes,
        embedding     = embedding,
        age           = age or analysis["estimated_age"],
        gender        = analysis["gender"],
        ai_notes      = analysis["notes"],
    )

    return JSONResponse(status_code=201, content={
        "success":    True,
        "user_id":    user.id,
        "name":       user.name,
        "age":        user.age,
        "gender":     user.gender,
        "position":   user.position,
        "face_image": user.face_image,
        "message":    "User registered successfully.",
    })


# ── Camera preview ────────────────────────────────────────────────────────────

@router.get("/preview", summary="Live camera preview frame (JPEG)")
def camera_preview():
    """Returns a single JPEG frame from Webcam #1 — use to check camera is working."""
    jpeg = capture_preview_frame()
    return Response(content=jpeg, media_type="image/jpeg")


# ── Add extra face photo ──────────────────────────────────────────────────────

@router.post("/user/{user_id}/face", summary="Add extra face photo to existing user")
async def add_face(
    user_id:    int,
    face_image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    face_bytes = await face_image.read()
    _validate(face_image, face_bytes)
    img_bgr   = decode_image(face_bytes)
    embedding = generate_embedding(img_bgr)
    record    = add_embedding(db, user_id, face_bytes, embedding)
    return {"success": True, "user_id": user_id, "embedding_id": record.id}


# ── List users ────────────────────────────────────────────────────────────────

@router.get("/users", summary="List all registered users")
def list_users(db: Session = Depends(get_db)):
    return [
        {
            "id":         u.id,
            "name":       u.name,
            "age":        u.age,
            "gender":     u.gender,
            "position":   u.position,
            "face_image": u.face_image,
            "image_user": u.image_user,
            "ai_notes":   u.ai_notes,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in get_all_users(db)
    ]


@router.get("/users/embeddings", summary="All embeddings for detection engine")
def all_embeddings(db: Session = Depends(get_db)):
    return load_all_embeddings(db)


# ── Delete user ───────────────────────────────────────────────────────────────

@router.delete("/user/{user_id}", summary="Delete user and all their data")
def remove_user(user_id: int, db: Session = Depends(get_db)):
    delete_user(db, user_id)
    return {"success": True, "message": f"User {user_id} deleted."}