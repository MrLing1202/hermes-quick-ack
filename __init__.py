"""
Quick-Ack Plugin for Hermes Agent
==================================

Sends an instant acknowledgment to the user when a message arrives,
before the main model starts processing. Two modes:

- **static**: Sends a fixed customizable message (zero latency)
- **smart**:  Calls a fast LLM to generate a contextual one-liner

The full response follows normally — the ack just tells the user
"Hermes heard you and is thinking."

Config (config.yaml)::

    plugins:
      entries:
        quick-ack:
          enabled: true
          mode: smart                    # 'static' or 'smart'
          ack_message: "收到，正在思考中..."  # used in static mode
          model:                          # used in smart mode
            provider: google
            model: gemini-2.0-flash
          max_length: 60                  # max chars for smart ack
          skip_patterns:                  # skip ack for these patterns
            - "^/"
            - "^\\\\s*$"
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default ack messages for static mode (rotated randomly for variety)
_FALLBACK_ACKS = [
    "收到，正在思考中…",
    "马上来，稍等一下～",
    "已收到，处理中 ⏳",
    "好的，让我想想…",
]

# System prompt for smart ack generation
_SMART_ACK_SYSTEM = (
    "You are a quick-responder bot. Given a user's message, generate a very short "
    "acknowledgment (under 30 characters in the same language as the user's message). "
    "Be natural, friendly, and varied. Don't repeat the user's words. "
    "Just acknowledge you received it and are working on it. "
    "Examples: '好的，让我看看', '收到，马上处理', 'Let me think about that...', "
    "'好的！这个有意思', 'Looking into it ⏳'. "
    "Reply with ONLY the acknowledgment, nothing else."
)

# Patterns to skip (commands, empty messages, etc.)
_DEFAULT_SKIP_PATTERNS = [
    r"^/",           # slash commands
    r"^\s*$",        # empty/whitespace
]


def _load_config() -> Dict[str, Any]:
    """Load plugin config from config.yaml."""
    try:
        from hermes_cli.config import cfg_get
        return cfg_get("plugins.entries.quick-ack", {}) or {}
    except Exception:
        return {}


def _should_skip(text: str, skip_patterns: list[str]) -> bool:
    """Check if message should skip the ack."""
    for pattern in skip_patterns:
        try:
            if re.search(pattern, text):
                return True
        except re.error:
            continue
    return False


async def _send_ack_via_gateway(
    gateway: Any,
    event: Any,
    message: str,
) -> None:
    """Send ack message through the platform adapter."""
    try:
        source = event.source
        platform = source.platform
        chat_id = str(source.chat_id)

        adapter = gateway.adapters.get(platform)
        if not adapter:
            logger.debug("quick-ack: no adapter for platform %s", platform)
            return

        await adapter.send(chat_id, message)
        logger.debug("quick-ack: sent ack to %s/%s", platform.value, chat_id)
    except Exception as exc:
        logger.warning("quick-ack: failed to send ack: %s", exc)


async def _generate_smart_ack(
    ctx: Any,
    user_text: str,
    model_config: Dict[str, Any],
    max_length: int,
) -> Optional[str]:
    """Call a fast LLM to generate a contextual ack."""
    try:
        provider = model_config.get("provider")
        model = model_config.get("model")

        messages = [
            {"role": "system", "content": _SMART_ACK_SYSTEM},
            {"role": "user", "content": user_text[:500]},  # truncate for speed
        ]

        result = await ctx.llm.acomplete(
            messages=messages,
            provider=provider,
            model=model,
            max_tokens=60,
            temperature=0.8,
        )

        if result and result.text:
            ack = result.text.strip().strip('"').strip("'")
            # Enforce max length
            if len(ack) > max_length:
                ack = ack[:max_length] + "…"
            return ack if ack else None

    except Exception as exc:
        logger.warning("quick-ack: smart ack generation failed: %s", exc)

    return None


def _on_pre_gateway_dispatch(
    *,
    event: Any,
    gateway: Any,
    session_store: Any,
    **kwargs: Any,
) -> Dict[str, str]:
    """Hook: fires before agent dispatch on every incoming message.

    Sends a quick ack asynchronously, then returns allow to continue
    normal processing.
    """
    config = _load_config()

    if not config.get("enabled", True):
        return {"action": "allow"}

    user_text = (event.text or "").strip()
    skip_patterns = config.get("skip_patterns", _DEFAULT_SKIP_PATTERNS)

    if _should_skip(user_text, skip_patterns):
        return {"action": "allow"}

    mode = config.get("mode", "smart")
    max_length = config.get("max_length", 60)

    if mode == "static":
        ack_message = config.get("ack_message", _FALLBACK_ACKS[0])
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_ack_via_gateway(gateway, event, ack_message))
        except RuntimeError:
            logger.debug("quick-ack: no event loop, skipping static ack")

    elif mode == "smart":
        model_config = config.get("model", {})
        if not model_config.get("model"):
            # Fallback to static if no model configured
            ack_message = config.get("ack_message", _FALLBACK_ACKS[0])
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send_ack_via_gateway(gateway, event, ack_message))
            except RuntimeError:
                pass
        else:
            # We need ctx for LLM access, but it's not in hook kwargs.
            # Use a closure captured during register().
            if hasattr(_on_pre_gateway_dispatch, "_ctx"):
                ctx = _on_pre_gateway_dispatch._ctx
                try:
                    loop = asyncio.get_running_loop()

                    async def _smart_ack():
                        ack = await _generate_smart_ack(
                            ctx, user_text, model_config, max_length
                        )
                        if ack:
                            await _send_ack_via_gateway(gateway, event, ack)
                        else:
                            # Fallback to static on LLM failure
                            fallback = config.get("ack_message", _FALLBACK_ACKS[0])
                            await _send_ack_via_gateway(gateway, event, fallback)

                    loop.create_task(_smart_ack())
                except RuntimeError:
                    logger.debug("quick-ack: no event loop, skipping smart ack")
            else:
                # No ctx available, fallback to static
                ack_message = config.get("ack_message", _FALLBACK_ACKS[0])
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_send_ack_via_gateway(gateway, event, ack_message))
                except RuntimeError:
                    pass

    return {"action": "allow"}


def register(ctx: Any) -> None:
    """Plugin entry point — register the pre_gateway_dispatch hook."""
    # Store ctx on the callback so the hook can access LLM
    _on_pre_gateway_dispatch._ctx = ctx

    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)

    logger.info("quick-ack: plugin registered (pre_gateway_dispatch hook)")
