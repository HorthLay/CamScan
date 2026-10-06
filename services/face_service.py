
"""
face_service.py
───────────────
Face engine facade:
  - UniFace (ONNX: SCRFD + ArcFace) by default — better accuracy, faster, and
    supports liveness / anti-spoofing.
  - InsightFace fallback when UniFace cannot load or FACE_BACKEND=insightface.
Handles:
  - Loading the chosen model (once)
  - Generating embeddings from images
  - Comparing embeddings for recognition
"""

import json
import logging
import os

import numpy as np
import cv2
from fastapi import HTTPException

logger = logging.getLogger("camscan.face")

# ── Engine selection ─────────────────────────────────────────────────────────

FACE_BACKEND = os.getenv("FACE_BACKEND", "uniface").strip().lower()


def _use_uniface():
    return FACE_BACKEND == "uniface"


def engine_status() -> dict:
    """Report the active engine + availability without forcing a model load."""
    import importlib.util

    def _spec(name):
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False

    if _use_uniface():
        return {"backend": "uniface", "ready": _spec("uniface")}
    return {"backend": "insightface", "ready": _spec("insightface")}


def _uniface_embedding(img_bgr: np.ndarray) -> np.ndarray:
    from services.uniface_service import generate_embedding
    return generate_embedding(img_bgr)


# ── InsightFace fallback singleton ───────────────────────────────────────────

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


def _insightface_embedding(img_bgr: np.ndarray) -> np.ndarray:
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


def generate_embedding(img_bgr: np.ndarray) -> np.ndarray:
    """
    Detect faces and return 512-d embedding of the largest face.
    Uses UniFace by default; falls back to InsightFace on engine failure.
    Raises 422 if no face is found.
    """
    if _use_uniface():
        try:
            return _uniface_embedding(img_bgr)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("UniFace failed (%s) — falling back to InsightFace.", exc)
    return _insightface_embedding(img_bgr)
 
 
def decode_image(data: bytes) -> np.ndarray:
    """Convert raw bytes → OpenCV BGR image."""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Cannot decode image.")
    return img


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


def _embedding_matrix(candidates: list[dict]) -> np.ndarray:
    """Parse all embedding JSON strings into a single (N, 512) float32 matrix."""
    matrix = np.empty((len(candidates), 512), dtype=np.float32)
    for i, candidate in enumerate(candidates):
        matrix[i] = json.loads(candidate["embedding"])
    return matrix


def _normalize_along_axis(matrix: np.ndarray) -> np.ndarray:
    return matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)


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
    if not candidates:
        return None

    # Vectorized cosine similarity: one matrix multiply instead of a Python loop.
    matrix  = _normalize_along_axis(_embedding_matrix(candidates))
    probe_n = probe.astype(np.float32) / (np.linalg.norm(probe) + 1e-10)
    scores  = matrix @ probe_n

    idx   = int(np.argmax(scores))
    score = float(scores[idx])

    if score >= threshold:
        return {**candidates[idx], "confidence": round(score, 4)}

    return None  # unknown face
 