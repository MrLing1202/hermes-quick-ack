"""
Quick-Ack Plugin v2 — 智能秒回，不是机器人废话
=================================================

用户发消息后，先用快模型回一句"像人说的话"，再用主模型深度回答。

三个模式：
- static  : 固定话术（最简单）
- smart   : 快模型根据上下文生成一句话（自然、有理解力）
- preview : 快模型直接给出简短回答预览，主模型随后给出完整版

Config (config.yaml)::

    plugins:
      entries:
        quick-ack:
          enabled: true
          mode: smart
          model:
            provider: google
            model: gemini-2.0-flash
          max_length: 80
          skip_patterns:
            - "^/"
            - "^\\s*$"
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts — 这是灵魂，决定了 ack 是"像人话"还是"像机器人"
# ---------------------------------------------------------------------------

_SMART_SYSTEM = """你是一个对话助手的"快速响应层"。用户刚发来一条消息，主模型正在深度思考中。

你的任务：回一句话，让对方知道你"听懂了"，而且正在认真对待。

要求：
1. 用和用户相同的语言回复
2. 像朋友聊天一样自然，不要客套、不要"收到"、不要"稍等"
3. 要体现你理解了对方的问题/需求，可以简单复述或回应关键词
4. 可以带一点个人风格（好奇、兴奋、认真），但不要浮夸
5. 20-50个字，不要超过一行
6. 绝对不要说"正在思考"、"处理中"、"稍等"这类话

好的例子：
- 用户："帮我写个爬虫" → "爬虫啊，抓哪个网站的数据？我先想想方案"
- 用户："这段代码报错了" → "报错了？让我看看什么情况"
- 用户："写一篇关于AI的文章" → "AI的文章，有字数要求吗？我先构思一下"
- 用户："解释一下量子计算" → "量子计算！这个话题有意思"
- 用户："debug this function" → "Let me take a close look at this function"
- 用户："帮我翻译成英文" → "好的，翻成英文，我来好好处理"
- 用户："今天天气怎么样" → "查天气是吧"

坏的例子：
- "收到，正在为您处理" ✗
- "好的，稍等一下" ✗
- "我来帮您解决这个问题" ✗
- "感谢您的提问" ✗

直接回复，不要加引号，不要加任何前缀。"""

_PREVIEW_SYSTEM = """你是一个对话助手的"快速预览层"。用户刚发来一条消息，主模型还在深度思考中。

你的任务：用1-2句话快速给出一个简短但有用的回答预览。不是确认收到，而是直接回答。

要求：
1. 用和用户相同的语言回复
2. 直接回答，不要说"让我想想"之类的话
3. 给出核心要点或方向，细节留给后面的完整回答
4. 30-80个字
5. 如果是代码问题，可以先指出可能的原因
6. 如果是知识问题，可以先给一句话结论

好的例子：
- 用户："Python怎么读取Excel" → "用pandas库，一行代码搞定：pd.read_excel('文件.xlsx')，详细用法我马上给你展开"
- 用户："解释一下REST API" → "简单说就是用HTTP动词（GET/POST/PUT/DELETE）操作资源的接口规范，我来给你详细讲讲"
- 用户："这段代码为什么慢" → "大概率是循环里反复查数据库了，批量查询会快很多。我来看看具体代码"

直接回复，不要加引号，不要加任何前缀。"""


def _load_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import cfg_get
        return cfg_get("plugins.entries.quick-ack", {}) or {}
    except Exception:
        return {}


def _should_skip(text: str, patterns: List[str]) -> bool:
    for p in patterns:
        try:
            if re.search(p, text):
                return True
        except re.error:
            continue
    return False


async def _send_ack(gateway: Any, event: Any, message: str) -> None:
    try:
        src = event.source
        adapter = gateway.adapters.get(src.platform)
        if adapter:
            await adapter.send(str(src.chat_id), message)
            logger.debug("quick-ack: sent to %s/%s", src.platform.value, src.chat_id)
    except Exception as exc:
        logger.warning("quick-ack: send failed: %s", exc)


async def _generate_ack(
    ctx: Any,
    user_text: str,
    model_cfg: Dict[str, Any],
    max_len: int,
    mode: str,
) -> Optional[str]:
    system = _PREVIEW_SYSTEM if mode == "preview" else _SMART_SYSTEM
    try:
        result = await ctx.llm.acomplete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text[:800]},
            ],
            provider=model_cfg.get("provider"),
            model=model_cfg.get("model"),
            max_tokens=120 if mode == "preview" else 60,
            temperature=0.9,
        )
        if result and result.text:
            ack = result.text.strip().strip('"').strip("'").strip("「」""")
            if len(ack) > max_len:
                ack = ack[:max_len] + "…"
            return ack if ack else None
    except Exception as exc:
        logger.warning("quick-ack: LLM call failed: %s", exc)
    return None


def _on_pre_gateway_dispatch(
    *, event: Any, gateway: Any, session_store: Any, **kw: Any,
) -> Dict[str, str]:
    cfg = _load_config()
    if not cfg.get("enabled", True):
        return {"action": "allow"}

    text = (event.text or "").strip()
    if _should_skip(text, cfg.get("skip_patterns", [r"^/", r"^\s*$"])):
        return {"action": "allow"}

    mode = cfg.get("mode", "smart")
    max_len = cfg.get("max_length", 80)
    model_cfg = cfg.get("model", {})

    ctx = getattr(_on_pre_gateway_dispatch, "_ctx", None)

    if mode == "static":
        msg = cfg.get("ack_message", "")
        if msg:
            try:
                asyncio.get_running_loop().create_task(_send_ack(gateway, event, msg))
            except RuntimeError:
                pass

    elif ctx and model_cfg.get("model"):
        try:
            loop = asyncio.get_running_loop()

            async def _do():
                ack = await _generate_ack(ctx, text, model_cfg, max_len, mode)
                if ack:
                    await _send_ack(gateway, event, ack)

            loop.create_task(_do())
        except RuntimeError:
            pass

    return {"action": "allow"}


def register(ctx: Any) -> None:
    _on_pre_gateway_dispatch._ctx = ctx
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    logger.info("quick-ack v2 registered")
