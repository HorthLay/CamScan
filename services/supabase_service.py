"""
supabase_service.py
───────────────────
Optional Supabase client for API access (auth, storage, realtime).
The database fallback in database.py uses SUPABASE_DATABASE_URL directly;
this helper is for working with the Supabase REST/SDK API.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("camscan.supabase")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()


def get_supabase_client():
    """Return an authenticated Supabase client, or None if not configured."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        logger.warning("SUPABASE_URL / SUPABASE_KEY not set — Supabase client disabled.")
        return None
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        logger.exception("Failed to create Supabase client: %s", exc)
        return None