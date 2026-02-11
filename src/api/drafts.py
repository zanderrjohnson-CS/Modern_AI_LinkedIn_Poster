"""
AI-powered post drafting using Google Gemini.

Generates LinkedIn post drafts based on a topic, category, and optional style/template.
"""

import os
import json
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


SYSTEM_PROMPT = """You are a LinkedIn content strategist. Write engaging LinkedIn posts that:
- Sound authentic and human (not corporate or AI-generated)
- Open with a strong hook in the first line
- Use short paragraphs and line breaks for readability
- Include a clear takeaway or call to action
- Are between 150-300 words unless told otherwise
- Avoid hashtags unless specifically asked
- Don't use emojis excessively (1-2 max if any)

Return ONLY the post text, nothing else. No preamble, no "Here's your post:", just the raw post content."""


def check_gemini_access() -> bool:
    """Check if Gemini API key is configured."""
    return bool(GEMINI_API_KEY) and GEMINI_API_KEY != "your_gemini_api_key_here"


def draft_post(
    topic: str,
    category: str | None = None,
    tone: str | None = None,
    template: str | None = None,
    example_post: str | None = None,
    max_words: int | None = None,
) -> str:
    """
    Generate a LinkedIn post draft using Gemini.

    Args:
        topic: What the post should be about.
        category: Content category (for context).
        tone: Desired tone (e.g., "professional", "casual", "storytelling").
        template: A template/structure to follow.
        example_post: An example post to match the style of.
        max_words: Approximate word limit.

    Returns:
        The generated post text.
    """
    if not check_gemini_access():
        raise RuntimeError(
            "GEMINI_API_KEY not set. Add it to your .env file:\n"
            "  GEMINI_API_KEY=your_key_here\n"
            "Get one at: https://aistudio.google.com/apikey"
        )

    # Build the user prompt
    parts = [f"Write a LinkedIn post about: {topic}"]

    if category:
        parts.append(f"Content category: {category}")
    if tone:
        parts.append(f"Tone: {tone}")
    if max_words:
        parts.append(f"Target length: approximately {max_words} words")
    if template:
        parts.append(f"Follow this structure:\n{template}")
    if example_post:
        parts.append(f"Match the style of this example post:\n---\n{example_post}\n---")

    user_prompt = "\n\n".join(parts)

    # Call Gemini API
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": SYSTEM_PROMPT + "\n\n" + user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 1024,
        },
    }

    resp = requests.post(
        f"{url}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )

    if resp.status_code != 200:
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message", resp.text)
        except ValueError:
            msg = resp.text
        raise RuntimeError(f"Gemini API error (HTTP {resp.status_code}): {msg}")


    data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip()
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response format: {json.dumps(data)[:300]}")


def refine_post(original: str, instruction: str) -> str:
    """
    Refine an existing draft based on feedback.

    Args:
        original: The current draft text.
        instruction: What to change (e.g., "make it shorter", "add a question at the end").

    Returns:
        The refined post text.
    """
    if not check_gemini_access():
        raise RuntimeError("GEMINI_API_KEY not set.")

    user_prompt = (
        f"Here is a LinkedIn post draft:\n---\n{original}\n---\n\n"
        f"Revise it with this feedback: {instruction}\n\n"
        f"Return ONLY the revised post text, nothing else."
    )

    url = GEMINI_URL.format(model=GEMINI_MODEL)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
        },
    }

    resp = requests.post(
        f"{url}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise RuntimeError("Unexpected Gemini response format")