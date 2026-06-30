"""
mistral_service.py
──────────────────
Uses Mistral AI vision to analyze a face photo and auto-fill:
  - name (guessed from appearance, placeholder)
  - estimated age
  - suggested position / role
  - gender (for context)
  - confidence notes
"""

import os
import base64
import json
import asyncio
from datetime import date
from fastapi import HTTPException
from dotenv import load_dotenv

try:
    from mistralai.client import Mistral
except ImportError:  # Newer SDKs expose Mistral at the package root.
    from mistralai import Mistral

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_AGENT_ID = os.getenv("MISTRAL_AGENT_ID", "ag_019f12454e1c719eaeb6258b095471d1")
MISTRAL_AGENT_VERSION = int(os.getenv("MISTRAL_AGENT_VERSION", "0"))


def _image_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _strip_markdown(content: str) -> str:
    return content.replace("```json", "").replace("```", "").strip()


def _find_json_text(value) -> str | None:
    if isinstance(value, str):
        content = _strip_markdown(value)
        if "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            return content[start:end]
        return None

    if isinstance(value, dict):
        for child in value.values():
            found = _find_json_text(child)
            if found:
                return found
        return None

    if isinstance(value, list):
        for child in value:
            found = _find_json_text(child)
            if found:
                return found
        return None

    if hasattr(value, "model_dump"):
        return _find_json_text(value.model_dump())

    if hasattr(value, "__dict__"):
        return _find_json_text(vars(value))

    return None


def _start_face_analysis_conversation(image_bytes: bytes):
    client = Mistral(api_key=MISTRAL_API_KEY)
    b64 = _image_to_base64(image_bytes)

    prompt = """You are a professional HR assistant analyzing a face photo for employee registration.

Analyze this face photo and respond ONLY with a valid JSON object. No extra text, no markdown, no explanation.

Return exactly this structure:
{
  "estimated_age": <integer, best estimate of person's age>,
  "gender": "<Male | Female | Unknown>",
  "notes": "<one short sentence observation about the photo quality or face visibility>"
}

Be respectful and professional. Focus only on age estimation and photo quality notes. 
Do NOT include suggested_position. If the photo is unclear or no face is visible, still return the JSON with nulls where needed."""

    inputs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                    },
                },
            ],
        }
    ]

    return client.beta.conversations.start(
        agent_id=MISTRAL_AGENT_ID,
        agent_version=MISTRAL_AGENT_VERSION,
        inputs=inputs,
    )


async def analyze_face(image_bytes: bytes) -> dict:
    """
    Send face image to the configured Mistral agent.
    Returns dict: { age, gender, position, notes }
    """
    if not MISTRAL_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="MISTRAL_API_KEY not set in .env"
        )

    try:
        response = await asyncio.to_thread(_start_face_analysis_conversation, image_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Mistral API error: {exc}"
        ) from exc

    content = _find_json_text(response)
    if not content:
        raise HTTPException(
            status_code=502,
            detail=f"Mistral returned no JSON content: {response}"
        )
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"Mistral returned invalid JSON: {content}"
        )

    return {
        "estimated_age":      result.get("estimated_age"),
        "gender":             result.get("gender", "Unknown"),
        "suggested_position": "",
        "notes":              result.get("notes", ""),
    }


def generate_ai_notes_from_user(name: str, age: Optional[int] = None, date_of_birth: Optional[date] = None, note: Optional[str] = None) -> str:
    """
    Generate AI notes based only on name, age/date_of_birth, and note.
    This creates a simple note without using external AI.
    Note can be: walkout, work, resign
    """
    from typing import Optional
    parts = []
    if name:
        parts.append(f"Name: {name}")
    
    if age:
        parts.append(f"Age: {age}")
    elif date_of_birth:
        calculated_age = (date.today().year - date_of_birth.year - 
                        ((date.today().month, date.today().day) < (date_of_birth.month, date_of_birth.day)))
        parts.append(f"Age: {calculated_age} (from DOB: {date_of_birth.isoformat()})")
    
    if note:
        note_detail = {
            "walkout": "User walked out / left the premises",
            "work": "User is currently working / active",
            "resign": "User has resigned / terminated",
        }.get(note.lower(), f"Note: {note}")
        parts.append(note_detail)
    
    return "; ".join(parts) if parts else ""
