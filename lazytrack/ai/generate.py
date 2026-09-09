from __future__ import annotations

import logging
from typing import Any

from lazytrack.ai.base import AIInvalidResponseError, AIProvider, AITimeoutError
from lazytrack.ai.normalize import ParseContext, extract_json_object, parse_intent
from lazytrack.ai.schemas import ClarificationRequired

logger = logging.getLogger(__name__)


async def generate_structured_intent(
    provider: AIProvider[Any],
    prompt: str,
    ctx: ParseContext,
):
    last_timeout: AITimeoutError | None = None
    raw: str | None = None
    for _ in range(2):
        try:
            result = await provider.generate(prompt)
            raw = result if isinstance(result, str) else result.model_dump_json()
            last_timeout = None
            break
        except AITimeoutError as e:
            last_timeout = e
            logger.warning("AI timeout, retrying once")
    if last_timeout is not None:
        return ClarificationRequired(
            reason="Sorry, the AI timed out. Please try again.",
            missing_fields=[],
        )

    intent = parse_intent(raw or "", ctx)
    if not isinstance(intent, ClarificationRequired):
        return intent
    parsed = extract_json_object(raw or "")
    prev_type = str((parsed or {}).get("type", "")).lower()
    if parsed and prev_type not in ("clarification_required", "clarificationrequired"):
        repair = (
            f"{prompt}\n\nYour previous JSON was invalid or incomplete:\n{raw[:800]}\n\n"
            "Fix it to a valid intent JSON. Dates must be YYYY-MM-DD. "
            "Calendar types may send date or dates. "
            "Use the matching type: allocate_time, show_week, add_leave, "
            "remove_leave, add_holiday, remove_holiday, or clarification_required."
        )
        try:
            repaired = await provider.generate(repair)
            repaired_text = repaired if isinstance(repaired, str) else repaired.model_dump_json()
            return parse_intent(repaired_text, ctx)
        except AITimeoutError:
            return ClarificationRequired(
                reason="Sorry, the AI timed out. Please try again.",
                missing_fields=[],
            )
        except AIInvalidResponseError:
            return intent
    return intent
