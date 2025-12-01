from __future__ import annotations

import json
import logging
from typing import List

from app.ai.openai_client import get_client
from app.core.config import settings

logger = logging.getLogger(__name__)


async def translate_texts_to_korean(texts: List[str]) -> List[str]:
    """
    Translate a list of texts to Korean, preserving order. If OpenAI 클라이언트가
    없거나 실패하면 원문을 그대로 반환한다.
    """
    if not texts:
        return texts

    client = get_client()
    if not client:
        return texts

    try:
        system_prompt = (
            "You are a translation helper. "
            "Translate each string in the provided JSON array to natural Korean. "
            "Keep place and restaurant names as-is when they are proper nouns, "
            "but translate descriptions and sentences. "
            "Return JSON with a key 'texts' containing the translated array in the same order."
        )
        resp = await client.chat.completions.create(
            model=settings.openai_model_chat,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({"texts": texts}, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        payload = json.loads(resp.choices[0].message.content)
        translated = payload.get("texts")
        if isinstance(translated, list) and len(translated) == len(texts):
            return [str(item) if item is not None else original for item, original in zip(translated, texts)]
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Translation failed, returning original texts: %s", exc)
    return texts


async def translate_text_to_korean(text: str) -> str:
    result = await translate_texts_to_korean([text])
    return result[0] if result else text
