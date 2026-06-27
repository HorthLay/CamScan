from typing import Optional
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
MAX_BYTES    = 10 * 1024 * 1024


def _validate(file: UploadFile, data: bytes):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Only JPEG/PNG/WEBP accepted.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB.")


@router.post("/capture", summary="3-2-1 voice countdown → capture face → Mistral analysis")
async def capture_and_analyze():
    jpeg_bytes = capture_with_countdown()
    img_bgr    = decode_image(jpeg_bytes)
    embedding  = generate_embedding(img_bgr)
    analysis   = await analyze_face(jpeg_bytes)

    import base64
    return {
        "success":            True,
        "image_base64":       base64.b64encode(jpeg_bytes).decode(),
        "estimated_age":      analysis["estimated_age"],
        "gender":             analysis["gender"],
        "suggested_position": analysis["suggested_position"],
        "ai_notes":           analysis["notes"],
        "message":            "Face captured. Confirm details then POST /register/user/confirm.",
    }


@router.post("/user/confirm", summary="Save captured + analyzed user to DB")
async def confirm_and_save(
    name:       str            = Form(...),
    position:   Optional[str]  = Form(None),
    age:        Optional[int]  = Form(None),
    gender:     Optional[str]  = Form(None),
    ai_notes:   Optional[str]  = Form(None),
    face_image: UploadFile     = File(...),
    image_user: UploadFile     = File(None),
    db: Session = Depends(get_db),
):
    face_bytes = await face_image.read()
    _validate(face_image, face_bytes)

    profile_bytes = None
    if image_user and image_user.filename:
        profile_bytes = await image_user.read()
        _validate(image_user, profile_bytes)

    img_bgr   = decode_image(face_bytes)
    embedding = generate_embedding(img_bgr)

    user = create_user(
        db=db, name=name, position=position,
        face_bytes=face_bytes, profile_bytes=profile_bytes,
        embedding=embedding, age=age, gender=gender, ai_notes=ai_notes,
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


@router.post("/user", summary="Register user by uploading photo manually")
async def register_user_manual(
    name:       str            = Form(...),
    position:   Optional[str]  = Form(None),
    age:        Optional[int]  = Form(None),
    face_image: UploadFile     = File(...),
    image_user: UploadFile     = File(None),
    db: Session = Depends(get_db),
):
    face_bytes = await face_image.read()
    _validate(face_image, face_bytes)

    profile_bytes = None
    if image_user and image_user.filename:
        profile_bytes = await image_user.read()
        _validate(image_user, profile_bytes)

    analysis  = await analyze_face(face_bytes)
    img_bgr   = decode_image(face_bytes)
    embedding = generate_embedding(img_bgr)

    user = create_user(
        db=db, name=name,
        position=position or analysis["suggested_position"],
        face_bytes=face_bytes, profile_bytes=profile_bytes,
        embedding=embedding,
        age=age or analysis["estimated_age"],
        gender=analysis["gender"],
        ai_notes=analysis["notes"],
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


@router.get("/preview", summary="Live camera preview frame (JPEG)")
def camera_preview():
    jpeg = capture_preview_frame()
    return Response(content=jpeg, media_type="image/jpeg")


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


@router.delete("/user/{user_id}", summary="Delete user and all their data")
def remove_user(user_id: int, db: Session = Depends(get_db)):
    delete_user(db, user_id)
    return {"success": True, "message": f"User {user_id} deleted."}