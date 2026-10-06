"""
uniface_service.py
──────────────────
UniFace-backed face engine (ONNX Runtime). Provides:
  - Face detection (SCRFD / RetinaFace) for live streams
  - Face recognition (ArcFace 512-d embeddings)
  - Anti-spoofing / liveness check (MiniFASNet) for camera security

Env knobs:
  FACE_BACKEND      uniface (default) | insightface  — engine used by face_service
  FACE_DETECTOR     scrfd_500m (fast default) | scrfd_10g (accurate) | retinaface
  DET_INPUT_SIZE    detection input size, default 640
  MIN_FACE_CONF     detection confidence floor, default 0.5
  ONNX_PROVIDERS    optional comma list, e.g. CUDAExecutionProvider,CPUExecutionProvider
  LIVENESS_CHECK    true/false — run anti-spoofing on /register/search
  LIVENESS_THRESHOLD  min liveness confidence to trust, default 0.6
"""

import logging
import os
import threading

import numpy as np
from fastapi import HTTPException

logger = logging.getLogger("camscan.uniface")

_analyzer = None
_spoofer = None
_engine_lock = threading.Lock()

MIN_FACE_CONF = float(os.getenv("MIN_FACE_CONF", "0.5"))
DET_INPUT_SIZE = int(os.getenv("DET_INPUT_SIZE", "640"))
LIVENESS_CHECK = os.getenv("LIVENESS_CHECK", "false").strip().lower() in ("1", "true", "yes")
LIVENESS_THRESHOLD = float(os.getenv("LIVENESS_THRESHOLD", "0.6"))


def liveness_enabled() -> bool:
    return LIVENESS_CHECK


def _providers():
    """Execution providers for ONNX sessions, or None to auto-detect (CUDA first)."""
    raw = os.getenv("ONNX_PROVIDERS", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return None


def _bbox_area(face) -> float:
    b = face.bbox
    return float((b[2] - b[0]) * (b[3] - b[1]))


def _build_detector():
    from uniface import RetinaFace, SCRFD
    from uniface.constants import RetinaFaceWeights, SCRFDWeights

    choice = os.getenv("FACE_DETECTOR", "scrfd_500m").strip().lower()
    size = (DET_INPUT_SIZE, DET_INPUT_SIZE)

    if choice in ("scrfd_10g", "scrfd-10g"):
        return SCRFD(
            model_name=SCRFDWeights.SCRFD_10G_KPS,
            confidence_threshold=MIN_FACE_CONF,
            input_size=size,
            providers=_providers(),
        )
    if choice in ("retinaface", "retina", "retinaface_mnet_v2"):
        return RetinaFace(
            model_name=RetinaFaceWeights.MNET_V2,
            confidence_threshold=MIN_FACE_CONF,
            input_size=size,
            providers=_providers(),
        )
    return SCRFD(
        model_name=SCRFDWeights.SCRFD_500M_KPS,
        confidence_threshold=MIN_FACE_CONF,
        input_size=size,
        providers=_providers(),
    )


def get_analyzer():
    """Lazy singleton: FaceAnalyzer = detector + ArcFace recognizer."""
    global _analyzer
    if _analyzer is None:
        from uniface import ArcFace, FaceAnalyzer

        with _engine_lock:
            if _analyzer is None:
                detector = _build_detector()
                try:
                    _analyzer = FaceAnalyzer(
                        detector=detector,
                        recognizer=ArcFace(providers=_providers()),
                    )
                except Exception as exc:
                    logger.warning("Custom UniFace setup failed (%s), using defaults.", exc)
                    _analyzer = FaceAnalyzer()
    return _analyzer


def get_spoofer():
    """Lazy singleton for the MiniFASNet anti-spoofing model."""
    global _spoofer
    if _spoofer is None:
        from uniface import MiniFASNet

        with _engine_lock:
            if _spoofer is None:
                _spoofer = MiniFASNet(providers=_providers())
    return _spoofer


def detect_faces(img_bgr: np.ndarray):
    """Detect faces without embeddings — fast path for live video streams.

    Returns a list of dicts: {bbox:[x1,y1,x2,y2], confidence, landmarks} or raises
    a generic Exception on engine failure so callers can fall back to Haar.
    """
    faces = get_analyzer().detector.detect(img_bgr)
    return [
        {
            "bbox": [int(v) for v in f.bbox],
            "confidence": float(f.confidence),
            "landmarks": f.landmarks.tolist() if f.landmarks is not None else None,
        }
        for f in faces
    ]


def analyze_faces(img_bgr: np.ndarray):
    """Run detection + ArcFace embedding on every face. Returns UniFace Face list."""
    return get_analyzer().analyze(img_bgr)


def generate_embedding(img_bgr: np.ndarray) -> np.ndarray:
    """512-d L2-normalized embedding of the largest detected face."""
    faces = [f for f in analyze_faces(img_bgr) if f.embedding is not None]
    if not faces:
        raise HTTPException(
            status_code=422,
            detail="No face detected. Please use a clear frontal photo.",
        )
    face = max(faces, key=_bbox_area)
    return np.asarray(face.embedding, dtype=np.float32)


def liveness_for_image(img_bgr: np.ndarray):
    """Anti-spoofing on the largest face. Returns dict or None when disabled/failed.

    Result: {"live": bool, "live_confidence": float, "threshold": float}
    """
    if not LIVENESS_CHECK:
        return None
    try:
        faces = detect_faces(img_bgr)
        faces = [f for f in faces if f["confidence"] >= MIN_FACE_CONF]
        if not faces:
            return None
        largest = max(faces, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))
        result = get_spoofer().predict(img_bgr, largest["bbox"])
        live = bool(result.is_real)
        confidence = float(result.confidence)
        # Reject anything that isn't clearly a live face: predicted-fake OR uncertain.
        rejected = (not live) or (confidence < LIVENESS_THRESHOLD)
        return {
            "live": live,
            "live_confidence": confidence,
            "threshold": LIVENESS_THRESHOLD,
            "rejected": rejected,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Liveness check unavailable: %s", exc)
        return None