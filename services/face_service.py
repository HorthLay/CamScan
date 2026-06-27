
"""
face_service.py
───────────────
Handles all InsightFace operations:
  - Loading the model (once)
  - Generating embeddings from images
  - Comparing embeddings for recognition
"""

import json
import numpy as np
import cv2
from fastapi import HTTPException
 
# ── Model singleton ──────────────────────────────────────────────────────────
 
_face_app = None
 
def get_face_app():
    """Load InsightFace once and reuse across all requests."""
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis
        _face_app = FaceAnalysis(
            name="buffalo_l",
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app
 
 
# ── Core functions ───────────────────────────────────────────────────────────
 
def decode_image(data: bytes) -> np.ndarray:
    """Convert raw bytes → OpenCV BGR image."""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Cannot decode image.")
    return img
 
 
def generate_embedding(img_bgr: np.ndarray) -> np.ndarray:
    """
    Detect faces and return 512-d embedding of the largest face.
    Raises 422 if no face is found.
    """
    app   = get_face_app()
    faces = app.get(img_bgr)
 
    if not faces:
        raise HTTPException(
            status_code=422,
            detail="No face detected. Please use a clear frontal photo."
        )
 
    # Pick the largest face if multiple detected
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return face.embedding   # shape (512,), float32
 
 
def embedding_to_json(embedding: np.ndarray) -> str:
    """Convert numpy embedding → JSON string for DB storage."""
    return json.dumps(embedding.tolist())
 
 
def json_to_embedding(json_str: str) -> np.ndarray:
    """Convert JSON string from DB → numpy array."""
    return np.array(json.loads(json_str), dtype=np.float32)
 
 
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute cosine similarity between two embeddings.
    Returns value between -1 and 1. Above 0.5 is typically a match.
    """
    a = a / (np.linalg.norm(a) + 1e-10)
    b = b / (np.linalg.norm(b) + 1e-10)
    return float(np.dot(a, b))
 
 
def find_best_match(
    probe: np.ndarray,
    candidates: list[dict],   # [{"user_id": int, "embedding": str, ...}, ...]
    threshold: float = 0.5,
) -> dict | None:
    """
    Compare probe embedding against all stored embeddings.
    Returns the best match dict or None if below threshold.
 
    candidates items must have keys: user_id, embedding (JSON str)
    """
    best_score  = -1.0
    best_match  = None
 
    for candidate in candidates:
        stored_emb = json_to_embedding(candidate["embedding"])
        score      = cosine_similarity(probe, stored_emb)
 
        if score > best_score:
            best_score = score
            best_match = candidate
 
    if best_score >= threshold:
        return {**best_match, "confidence": round(best_score, 4)}
 
    return None  # unknown face
 