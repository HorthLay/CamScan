"""
user_service.py
───────────────
All database operations for users and embeddings.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User, FaceEmbedding
from services.face_service import embedding_to_json

FACE_DIR    = Path("uploads/faces")
PROFILE_DIR = Path("uploads/profiles")
FACE_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def save_file(data: bytes, directory: Path, user_id: int) -> str:
    filename = f"{user_id}_{uuid.uuid4().hex}.jpg"
    path     = directory / filename
    path.write_bytes(data)
    return str(path).replace("\\", "/")


def create_user(
    db:            Session,
    name:          str,
    position:      Optional[str],
    face_bytes:    bytes,
    profile_bytes: Optional[bytes],
    embedding,
    age:           Optional[int]   = None,
    gender:        Optional[str]   = None,
    ai_notes:      Optional[str]   = None,
) -> User:
    emb_json = embedding_to_json(embedding)

    user = User(
        name          = name,
        position      = position,
        age           = age,
        gender        = gender,
        ai_notes      = ai_notes,
        face_embeding = emb_json,
        created_at    = datetime.utcnow(),
    )
    db.add(user)
    db.flush()

    user.face_image = save_file(face_bytes, FACE_DIR, user.id)
    if profile_bytes:
        user.image_user = save_file(profile_bytes, PROFILE_DIR, user.id)

    emb_record = FaceEmbedding(
        user_id    = user.id,
        embedding  = emb_json,
        created_at = datetime.utcnow(),
    )
    db.add(emb_record)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def get_all_users(db: Session) -> List[User]:
    return db.query(User).order_by(User.created_at.desc()).all()


def delete_user(db: Session, user_id: int):
    user = get_user(db, user_id)
    db.delete(user)
    db.commit()


def add_embedding(db: Session, user_id: int, face_bytes: bytes, embedding) -> FaceEmbedding:
    user     = get_user(db, user_id)
    emb_json = embedding_to_json(embedding)

    user.face_image    = save_file(face_bytes, FACE_DIR, user.id)
    user.face_embeding = emb_json

    record = FaceEmbedding(
        user_id    = user_id,
        embedding  = emb_json,
        created_at = datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    return record


def load_all_embeddings(db: Session) -> List[dict]:
    rows = (
        db.query(FaceEmbedding, User)
        .join(User, FaceEmbedding.user_id == User.id)
        .all()
    )
    return [
        {
            "embedding_id": emb.id,
            "user_id":      user.id,
            "name":         user.name,
            "position":     user.position,
            "age":          user.age,
            "gender":       user.gender,
            "face_image":   user.face_image,
            "image_user":   user.image_user,
            "ai_notes":     user.ai_notes,
            "embedding":    emb.embedding,
        }
        for emb, user in rows
    ]