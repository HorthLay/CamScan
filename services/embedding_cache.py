"""
embedding_cache.py
──────────────────
In-memory cache for the user embedding list consumed by the detection engine.
Faces are searched on every /register/search, and re-parsing all stored JSON
embeddings plus a DB join each time gets slow as the user base grows.

The cache is invalidated automatically whenever this app creates / deletes a
user or adds an embedding (see user_service mutators).
"""

import threading
from typing import Callable, List, Optional

_lock = threading.Lock()
_cache: Optional[List[dict]] = None


def get_embeddings(loader: Callable[[], List[dict]]) -> List[dict]:
    """Return cached embeddings, loading fresh data only when the cache is cold."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = loader()
        return _cache


def invalidate_embeddings() -> None:
    """Drop the cache. The next get_embeddings() call reloads from the DB."""
    global _cache
    with _lock:
        _cache = None