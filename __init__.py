"""
Quick-Ack Plugin v3 — 智能秒回，深度优化版
===========================================

用户发消息后，先用快模型回一句"像人说的话"，主模型随后深度回答。

三个模式：
- static  : 15条内置中文消息随机选，零延迟零开销
- smart   : 快模型根据上下文生成自然回复（多语言匹配）
- preview : 简单问题直接答，复杂问题给方向性预览

v3 新增：
- cooldown 冷却机制（同一 chat_id N 秒内不重复 ack）
- 语言检测（中/英/日/韩），ack 语言自动匹配
- 内存统计计数器（总 ack 数、成功率、各模式使用次数）
- 最小消息长度过滤（<2 字符不发 ack）
- 全异步任务 try-except 兜底
- prompt 重写：无例子、更多样、防止模板化

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
          cooldown_seconds: 5
          min_text_length: 2
          skip_patterns:
            - "^/"
            - "^\\s*$"
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

# 冷却记录：chat_id → 上次发送 ack 的时间戳
_cooldown_map: Dict[str, float] = {}

# 统计计数器
_stats: Dict[str, int] = {
    "total": 0,       # 总 ack 发送次数
    "success": 0,     # 发送成功次数
    "failed": 0,      # 发送失败次数（含 LLM 失败 + adapter 失败）
    "static": 0,      # static 模式使用次数
    "smart": 0,       # smart 模式使用次数
    "preview": 0,     # preview 模式使用次数
    "cooldown_skip": 0,  # 被冷却跳过次数
    "filter_skip": 0,   # 被过滤跳过次数
}


# ---------------------------------------------------------------------------
# 静态模式：15 条多样化中文消息
# ---------------------------------------------------------------------------

_STATIC_MESSAGES: List[str] = [
    "嗯，我看看",
    "来了来了",
    "收到，想想怎么回",
    "好嘞，琢磨一下",
    "在看了在看了",
    "稍等，马上给你弄",
    "了解，我先过一遍",
    "行，这个我来",
    "看到了，等我一下",
    "好的，让我理理",
    "嗯嗯，正在想",
    "明白你的意思了",
    "好，我先看看",
    "收到，想一下",
    "懂了，稍等哈",
]


# ---------------------------------------------------------------------------
# 语言检测
# ---------------------------------------------------------------------------

# 各语言 Unicode 范围的正则模式
_LANG_PATTERNS: List[tuple] = [
    # 日语平假名 + 片假名
    ("ja", re.compile(r'[぀-ゟ゠-ヿ]')),
    # 韩语谚文
    ("ko", re.compile(r'[가-힯ᄀ-ᇿ]')),
    # 中文汉字（CJK 统一汉字基本区 + 扩展 A 区）
    ("zh", re.compile(r'[一-鿿㐀-䶿]')),
]

# 各语言对应的确认/连接词，用于 LLM prompt 提示
_LANG_HINTS: Dict[str, str] = {
    "zh": "用中文回复",
    "en": "Reply in English",
    "ja": "日本語で返信してください",
    "ko": "한국어로 답변해 주세요",
}


def _detect_language(text: str) -> str:
    """
    检测文本的主要语言。
    优先匹配 CJK 范围，无匹配时默认英文。
    """
    for lang, pattern in _LANG_PATTERNS:
        if pattern.search(text):
            return lang
    # 含有拉丁字母则为英文
    if re.search(r'[a-zA-Z]', text):
        return "en"
    return "en"


# ---------------------------------------------------------------------------
# System Prompts — 无例子、无模板、让模型自由发挥
# ---------------------------------------------------------------------------

_SMART_SYSTEM = """\
你是对话中的"快速响应层"。用户刚发来消息，主模型正在深度思考。

你的唯一任务：用一句话回应，让用户感到被理解了。

风格要求：
- 像朋友聊天，自然随性，不要客套
- 体现你理解了对方的意图，可以点出关键词或核心诉求
- 可以带情绪：好奇、感兴趣、认真、兴奋——但不过度
- 绝对禁止说"收到""稍等""正在处理""我来帮您"这类机器人话术
- 20~50字，一行之内
- {lang_hint}

只输出回复内容本身，不要引号，不要任何前缀。"""

_PREVIEW_SYSTEM = """\
你是对话中的"快速预览层"。用户刚发来消息，主模型还在深度思考。

你的任务：快速给出有用的回答预览——不是确认收到，而是直接回答。

处理策略：
- 简单问题（常识、定义、单步操作）→ 直接给出答案
- 复杂问题（多步骤、代码调试、长文写作）→ 给出核心方向或切入点
- 不确定的问题 → 给出你的初步判断

风格要求：
- 像朋友随口说的建议，不像在念教科书
- 简洁自然，不啰嗦
- 30~80字
- {lang_hint}

只输出回复内容本身，不要引号，不要任何前缀。"""


# ---------------------------------------------------------------------------
# 配置与工具函数
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """从 hermes-cli 配置系统加载插件配置。"""
    try:
        from hermes_cli.config import cfg_get
        return cfg_get("plugins.entries.quick-ack", {}) or {}
    except Exception:
        return {}


def _should_skip(text: str, patterns: List[str]) -> bool:
    """检查消息是否匹配跳过模式（命令、空消息等）。"""
    for p in patterns:
        try:
            if re.search(p, text):
                return True
        except re.error:
            continue
    return False


def _make_chat_key(event: Any) -> str:
    """
    生成 chat 级别的冷却 key。
    格式：platform:chat_id
    """
    try:
        src = event.source
        return f"{src.platform}:{src.chat_id}"
    except Exception:
        return "unknown"


def _check_cooldown(chat_key: str, cooldown_sec: float) -> bool:
    """
    检查是否在冷却期内。
    返回 True 表示应该跳过（还在冷却中），False 表示可以发送。
    """
    now = time.monotonic()
    last = _cooldown_map.get(chat_key)
    if last is not None and (now - last) < cooldown_sec:
        return True
    return False


def _update_cooldown(chat_key: str) -> None:
    """更新冷却时间戳。"""
    _cooldown_map[chat_key] = time.monotonic()


def _cleanup_cooldown(max_age: float = 600.0) -> None:
    """
    清理过期的冷却记录，防止内存泄漏。
    保留最近 max_age 秒内的记录。
    """
    now = time.monotonic()
    expired = [k for k, v in _cooldown_map.items() if (now - v) > max_age]
    for k in expired:
        del _cooldown_map[k]


def get_stats() -> Dict[str, int]:
    """返回当前统计计数器的快照（供外部查询）。"""
    return dict(_stats)


# ---------------------------------------------------------------------------
# 核心异步函数
# ---------------------------------------------------------------------------

async def _send_ack(gateway: Any, event: Any, message: str) -> bool:
    """
    通过 adapter 发送 ack 消息。
    返回 True 表示成功，False 表示失败。
    """
    try:
        src = event.source
        adapter = gateway.adapters.get(src.platform)
        if adapter:
            await adapter.send(str(src.chat_id), message)
            logger.debug("quick-ack v3: 已发送 %s/%s", src.platform, src.chat_id)
            return True
        logger.debug("quick-ack v3: adapter 未找到 %s", src.platform)
    except Exception as exc:
        logger.warning("quick-ack v3: 发送失败: %s", exc)
    return False


async def _generate_ack(
    ctx: Any,
    user_text: str,
    model_cfg: Dict[str, Any],
    max_len: int,
    mode: str,
) -> Optional[str]:
    """
    调用快模型生成 ack 文本。
    根据检测到的语言调整 system prompt。
    """
    try:
        # 检测用户消息语言，动态注入提示
        lang = _detect_language(user_text)
        lang_hint = _LANG_HINTS.get(lang, _LANG_HINTS["en"])

        if mode == "preview":
            system = _PREVIEW_SYSTEM.format(lang_hint=lang_hint)
            max_tokens = 120
        else:
            system = _SMART_SYSTEM.format(lang_hint=lang_hint)
            max_tokens = 60

        result = await ctx.llm.acomplete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text[:800]},
            ],
            provider=model_cfg.get("provider"),
            model=model_cfg.get("model"),
            max_tokens=max_tokens,
            temperature=0.9,
        )
        if result and result.text:
            # 清理各种引号包裹：英文引号、中文引号、日文括号
            _strip_chars = '"\'`「」""'''
            ack = result.text.strip().strip(_strip_chars)
            if len(ack) > max_len:
                ack = ack[:max_len] + "…"
            return ack if ack else None
    except Exception as exc:
        logger.warning("quick-ack v3: LLM 调用失败: %s", exc)
    return None


async def _ack_pipeline(
    ctx: Any,
    gateway: Any,
    event: Any,
    text: str,
    cfg: Dict[str, Any],
    mode: str,
    chat_key: str,
) -> None:
    """
    完整的 ack 管道：生成 → 发送 → 统计 → 更新冷却。
    作为 fire-and-forget task 运行，内部全 try-except 兜底。
    """
    try:
        max_len = cfg.get("max_length", 80)
        model_cfg = cfg.get("model", {})

        # 记录总 ack 尝试数
        _stats["total"] += 1
        _stats[mode] = _stats.get(mode, 0) + 1

        # 生成 ack 文本
        ack = await _generate_ack(ctx, text, model_cfg, max_len, mode)
        if not ack:
            _stats["failed"] += 1
            return

        # 发送 ack
        ok = await _send_ack(gateway, event, ack)
        if ok:
            _stats["success"] += 1
            _update_cooldown(chat_key)
        else:
            _stats["failed"] += 1
    except Exception as exc:
        _stats["failed"] += 1
        logger.warning("quick-ack v3: 管道异常: %s", exc)


async def _static_ack(
    gateway: Any,
    event: Any,
    chat_key: str,
) -> None:
    """
    static 模式的 ack 管道：随机选一条内置消息 → 发送 → 统计。
    """
    try:
        _stats["total"] += 1
        _stats["static"] += 1

        msg = random.choice(_STATIC_MESSAGES)
        ok = await _send_ack(gateway, event, msg)
        if ok:
            _stats["success"] += 1
            _update_cooldown(chat_key)
        else:
            _stats["failed"] += 1
    except Exception as exc:
        _stats["failed"] += 1
        logger.warning("quick-ack v3: static 管道异常: %s", exc)


# ---------------------------------------------------------------------------
# Hook 入口
# ---------------------------------------------------------------------------

def _on_pre_gateway_dispatch(
    *, event: Any, gateway: Any, session_store: Any, **kw: Any,
) -> Dict[str, str]:
    """
    pre_gateway_dispatch hook 入口。
    同步函数，不阻塞主流程，通过 create_task fire-and-forget。
    """
    cfg = _load_config()

    # 插件未启用，直接放行
    if not cfg.get("enabled", True):
        return {"action": "allow"}

    # 提取并清理消息文本
    text = (event.text or "").strip()

    # 消息太短（<2字符）直接跳过
    min_len = cfg.get("min_text_length", 2)
    if len(text) < min_len:
        _stats["filter_skip"] += 1
        return {"action": "allow"}

    # 匹配跳过模式（命令、空白等）
    if _should_skip(text, cfg.get("skip_patterns", [r"^/", r"^\s*$"])):
        return {"action": "allow"}

    # 冷却检查
    chat_key = _make_chat_key(event)
    cooldown_sec = cfg.get("cooldown_seconds", 5)
    if _check_cooldown(chat_key, float(cooldown_sec)):
        _stats["cooldown_skip"] += 1
        return {"action": "allow"}

    # 定期清理过期冷却记录（大约每 100 次 hook 调用清理一次）
    if _stats["total"] % 100 == 0:
        try:
            _cleanup_cooldown()
        except Exception:
            pass

    mode = cfg.get("mode", "smart")
    ctx = getattr(_on_pre_gateway_dispatch, "_ctx", None)

    try:
        loop = asyncio.get_running_loop()

        if mode == "static":
            # static 模式：不需要 LLM，直接发内置消息
            loop.create_task(_static_ack(gateway, event, chat_key))
        elif ctx and cfg.get("model", {}).get("model"):
            # smart / preview 模式：调快模型生成
            loop.create_task(
                _ack_pipeline(ctx, gateway, event, text, cfg, mode, chat_key)
            )
    except RuntimeError:
        # 没有运行中的事件循环，忽略
        pass

    return {"action": "allow"}


# ---------------------------------------------------------------------------
# 注册入口（外部接口）
# ---------------------------------------------------------------------------

def register(ctx: Any) -> None:
    """
    插件注册入口。由 hermes-cli 框架调用。
    保持接口不变，通过闭包捕获 ctx。
    """
    _on_pre_gateway_dispatch._ctx = ctx
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    logger.info("quick-ack v3 已注册 (cooldown=%ss)", _load_config().get("cooldown_seconds", 5))
