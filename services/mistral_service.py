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
import httpx
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "pixtral-12b-2409"   # Mistral vision model


def _image_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


async def analyze_face(image_bytes: bytes) -> dict:
    """
    Send face image to Mistral Pixtral vision model.
    Returns dict: { age, gender, position, notes }
    """
    if not MISTRAL_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="MISTRAL_API_KEY not set in .env"
        )

    b64 = _image_to_base64(image_bytes)

    prompt = """You are a professional HR assistant analyzing a face photo for employee registration.

Analyze this face photo and respond ONLY with a valid JSON object — no extra text, no markdown, no explanation.

Return exactly this structure:
{
  "estimated_age": <integer, best estimate of person's age>,
  "gender": "<Male | Female | Unknown>",
  "suggested_position": "<a realistic job title based on appearance and context, e.g. Engineer, Manager, Staff, Security, etc.>",
  "notes": "<one short sentence observation about the photo quality or face visibility>"
}

Be respectful and professional. If the photo is unclear or no face is visible, still return the JSON with nulls where needed."""

    payload = {
        "model": MISTRAL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "max_tokens": 300,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            MISTRAL_API_URL,
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type":  "application/json",
            },
            json=payload,
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Mistral API error: {response.text}"
        )

    data    = response.json()
    content = data["choices"][0]["message"]["content"].strip()

    # Strip any accidental markdown fences
    content = content.replace("```json", "").replace("```", "").strip()

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
        "suggested_position": result.get("suggested_position", ""),
        "notes":              result.get("notes", ""),
    }