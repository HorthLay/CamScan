from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, File, Form, Request, UploadFile, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from services.face_service import decode_image, generate_embedding, find_best_match
from services.user_service import (
    create_user, get_user, get_all_users,
    delete_user, add_embedding, load_all_embeddings,
)
from services.capture_service import (
    build_countdown_audio,
    capture_frame,
    capture_preview_frame,
    capture_with_countdown,
)
from services.mistral_service import analyze_face, generate_ai_notes_from_user

router = APIRouter(prefix="/register", tags=["Registration"])

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES    = 10 * 1024 * 1024


def _validate(file: UploadFile, data: bytes):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Only JPEG/PNG/WEBP accepted.")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB.")


@router.get("/countdown-audio", summary="Browser-playable registration countdown sound")
def countdown_audio():
    return Response(
        content=build_countdown_audio(),
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Countdown-Duration": "4",
        },
    )


@router.post("/search", summary="Capture face and search existing users")
async def capture_and_search(
    server_countdown: bool = False,
    db: Session = Depends(get_db),
):
    if server_countdown:
        jpeg_bytes = capture_with_countdown()
    else:
        jpeg_bytes = capture_frame()

    img_bgr          = decode_image(jpeg_bytes)
    probe_embedding  = generate_embedding(img_bgr)
    candidates       = load_all_embeddings(db)
    match            = find_best_match(probe_embedding, candidates, threshold=0.55)

    import base64
    response = {
        "success":      True,
        "matched":      match is not None,
        "image_base64": base64.b64encode(jpeg_bytes).decode(),
        "message":      "User matched." if match else "No matching user found.",
    }

    if match:
        # Regenerate AI notes with note detail included
        from datetime import date as date_type
        date_of_birth = match.get("date_of_birth")
        dob = date_type.fromisoformat(date_of_birth) if date_of_birth else None
        ai_notes_with_note = generate_ai_notes_from_user(
            name=match.get("name") or "",
            age=match.get("age"),
            date_of_birth=dob,
            note=match.get("note")
        )
        
        response["user"] = {
            "id":           match.get("user_id"),
            "name":         match.get("name"),
            "age":          match.get("age"),
            "date_of_birth": match.get("date_of_birth"),
            "gender":       match.get("gender"),
            "position":     match.get("position"),
            "face_image":   match.get("face_image"),
            "image_user":   match.get("image_user"),
            "ai_notes":     ai_notes_with_note or match.get("ai_notes") or "",
            "note":         match.get("note"),
            "confidence":   match.get("confidence"),
        }

    return response


@router.post("/user/confirm", summary="Save captured + analyzed user to DB")
async def confirm_and_save(
    name:         str            = Form(...),
    position:     Optional[str]  = Form(None),
    age:          Optional[int]  = Form(None),
    date_of_birth: Optional[date] = Form(None),
    gender:       Optional[str]  = Form(None),
    note:         Optional[str]  = Form(None),
    ai_notes:     Optional[str]  = Form(None),
    face_image:   UploadFile     = File(...),
    image_user:   UploadFile     = File(None),
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

    if not ai_notes:
        ai_notes = generate_ai_notes_from_user(name, age, date_of_birth, note)

    user = create_user(
        db=db, name=name, position=position,
        face_bytes=face_bytes, profile_bytes=profile_bytes,
        embedding=embedding, age=age, gender=gender, ai_notes=ai_notes,
        date_of_birth=date_of_birth, note=note,
    )

    return JSONResponse(status_code=201, content={
        "success":    True,
        "user_id":    user.id,
        "name":       user.name,
        "age":        user.age,
        "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "gender":     user.gender,
        "position":   user.position,
        "face_image": user.face_image,
        "image_user": user.image_user,
        "ai_notes":   user.ai_notes,
        "note":       user.note if user.note else None,
        "message":    "User registered and face embedding saved.",
    })


@router.post("/user", summary="Register user by uploading photo manually")
async def register_user_manual(
    name:         str            = Form(...),
    position:     Optional[str]  = Form(None),
    age:          Optional[int]  = Form(None),
    date_of_birth: Optional[date] = Form(None),
    note:         Optional[str]  = Form(None),
    face_image:   UploadFile     = File(...),
    image_user:   UploadFile     = File(None),
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

    calculated_age = age or analysis["estimated_age"]
    ai_notes = generate_ai_notes_from_user(name, calculated_age, date_of_birth, note)
    if analysis["notes"]:
        ai_notes = f"{ai_notes}; {analysis['notes']}" if ai_notes else analysis["notes"]

    user = create_user(
        db=db, name=name,
        position=position,
        face_bytes=face_bytes, profile_bytes=profile_bytes,
        embedding=embedding,
        age=calculated_age,
        date_of_birth=date_of_birth,
        note=note,
        gender=analysis["gender"],
        ai_notes=ai_notes,
    )

    return JSONResponse(status_code=201, content={
        "success":    True,
        "user_id":    user.id,
        "name":       user.name,
        "age":        user.age,
        "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "gender":     user.gender,
        "position":   user.position,
        "face_image": user.face_image,
        "note":       user.note if user.note else None,
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
    users = get_all_users(db)
    result = []
    for u in users:
        # Regenerate AI notes with note detail included
        ai_notes_with_note = generate_ai_notes_from_user(
            name=u.name or "",
            age=u.age,
            date_of_birth=u.date_of_birth,
            note=u.note if u.note else None
        )
        result.append({
            "id":           u.id,
            "name":         u.name,
            "age":          u.age,
            "date_of_birth": u.date_of_birth.isoformat() if u.date_of_birth else None,
            "gender":       u.gender,
            "position":     u.position,
            "face_image":   u.face_image,
            "image_user":   u.image_user,
            "ai_notes":     ai_notes_with_note or u.ai_notes or "",
            "note":         u.note if u.note else None,
            "created_at":   u.created_at.isoformat() if u.created_at else None,
        })
    return result


@router.get("/users/embeddings", summary="All embeddings for detection engine")
def all_embeddings(db: Session = Depends(get_db)):
    return load_all_embeddings(db)


@router.delete("/user/{user_id}", summary="Delete user and all their data")
def remove_user(user_id: int, db: Session = Depends(get_db)):
    delete_user(db, user_id)
    return {"success": True, "message": f"User {user_id} deleted."}


@router.put("/user/{user_id}", summary="Update user info (name, dob, age, note, ai_notes)")
async def update_user_info(
    user_id:      int,
    request:      Request,
    name:        Optional[str]   = None,
    date_of_birth: Optional[str] = None,  # Accept as string for Laravel compatibility
    age:         Optional[int]   = None,
    note:        Optional[str]   = None,
    ai_notes:    Optional[str]   = None,
    db: Session = Depends(get_db),
):
    from datetime import date as date_type
    from services.user_service import get_user, sync_user_to_laravel
    
    # Try parsing from request body/form if available (Laravel sends JSON PUT)
    body_data = {}
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in content_type:
            body_data = await request.json()
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            body_data = {k: v for k, v in form.items()}
    except Exception as e:
        print(f"DEBUG: Failed to parse PUT body/form data: {e}")

    # Merge body data with query parameters (body/form takes precedence if present)
    name = body_data.get("name") if body_data.get("name") is not None else name
    date_of_birth = body_data.get("date_of_birth") if body_data.get("date_of_birth") is not None else date_of_birth
    
    body_age = body_data.get("age")
    if body_age is not None:
        try:
            age = int(body_age)
        except ValueError:
            pass
            
    note = body_data.get("note") if body_data.get("note") is not None else note
    ai_notes = body_data.get("ai_notes") if body_data.get("ai_notes") is not None else ai_notes
    
    user = get_user(db, user_id)
    
    if name:
        user.name = name
    
    # Parse date_of_birth string to date object if provided
    if date_of_birth:
        try:
            if isinstance(date_of_birth, str):
                date_of_birth = date_type.fromisoformat(date_of_birth)
        except (ValueError, TypeError):
            date_of_birth = None
    
    if date_of_birth:
        user.date_of_birth = date_of_birth
        user.update_age_from_dob()
    elif age is not None:
        user.age = age
    if note is not None:
        user.note = note
    
    # Regenerate AI notes with updated info if ai_notes not explicitly provided
    if ai_notes is not None:
        user.ai_notes = ai_notes
    else:
        user.ai_notes = generate_ai_notes_from_user(
            name=user.name or "",
            age=user.age,
            date_of_birth=user.date_of_birth,
            note=user.note if user.note else None
        )
    
    db.commit()
    db.refresh(user)
    
    # Sync to Laravel
    sync_user_to_laravel(user)
    
    return {
        "success": True,
        "user_id": user.id,
        "name": user.name,
        "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "age": user.age,
        "ai_notes": user.ai_notes,
        "note": user.note if user.note else None,
        "message": f"User {user_id} updated.",
    }


@router.post("/sync-from-laravel", summary="Receive user updates from Laravel web app")
async def sync_from_laravel(request: Request, db: Session = Depends(get_db)):
    """
    Endpoint for Laravel to sync user updates back to FastAPI.
    Accepts either form data or JSON from the web app when a user is edited.
    """
    from datetime import date as date_type

    content_type = request.headers.get("content-type", "").lower()
    try:
        if content_type.startswith("application/json"):
            payload = await request.json()
        else:
            form = await request.form()
            payload = {key: value for key, value in form.items()}
    except Exception as exc:
        print(f"DEBUG: Failed to parse Laravel sync payload: {exc}")
        payload = {}

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id is required")
    if isinstance(user_id, str):
        try:
            user_id = int(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="user_id must be an integer") from exc

    name = payload.get("name")
    date_of_birth = payload.get("date_of_birth")
    age = payload.get("age")
    note = payload.get("note")
    ai_notes = payload.get("ai_notes")

    if isinstance(age, str):
        try:
            age = int(age)
        except ValueError:
            age = None

    print(f"DEBUG: Received Laravel sync for user_id={user_id}, name={name}, date_of_birth={date_of_birth}, age={age}, note={note}, ai_notes={ai_notes}")

    user = get_user(db, user_id)

    updated = False

    if name is not None and name != (user.name or ""):
        user.name = name
        updated = True
        print(f"DEBUG: Updated name to {name}")

    if date_of_birth is not None:
        try:
            if isinstance(date_of_birth, datetime):
                dob = date_of_birth.date()
            elif isinstance(date_of_birth, date):
                dob = date_of_birth
            else:
                dob = date_type.fromisoformat(str(date_of_birth))

            if user.date_of_birth != dob:
                user.date_of_birth = dob
                user.update_age_from_dob()
                updated = True
                print(f"DEBUG: Updated date_of_birth to {date_of_birth}")
        except (ValueError, TypeError) as e:
            print(f"DEBUG: Error parsing date_of_birth: {e}")
    elif age is not None and age != (user.age or 0):
        user.age = age
        updated = True
        print(f"DEBUG: Updated age to {age}")

    if note is not None and note != (user.note or ""):
        user.note = note
        updated = True
        print(f"DEBUG: Updated note to {note}")

    if ai_notes is not None:
        user.ai_notes = ai_notes
        updated = True
        print(f"DEBUG: Updated ai_notes to {ai_notes}")
    elif updated:
        user.ai_notes = generate_ai_notes_from_user(
            name=user.name or "",
            age=user.age,
            date_of_birth=user.date_of_birth,
            note=user.note if user.note else None
        )
        print(f"DEBUG: Regenerated ai_notes")

    if updated:
        db.commit()
        db.refresh(user)
        print(f"DEBUG: Committed changes for user {user_id}")
    else:
        print(f"DEBUG: No changes detected for user {user_id}")

    return {
        "success": True,
        "user_id": user.id,
        "name": user.name,
        "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
        "age": user.age,
        "ai_notes": user.ai_notes,
        "note": user.note if user.note else None,
        "message": f"User {user_id} synced from Laravel.",
    }
