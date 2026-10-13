"""ctx-prune — Layer-1 context pruning via Morph Compact.

Observer hook (transform_tool_result): oversized tool outputs are compacted
through POST /v1/compact (native endpoint — NOT the chat shim, which silently
drops preserve_recent) before they enter model context.

Fail-open everywhere: any error, timeout, or missing key returns None and the
original tool result passes through untouched.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("ctx-prune")

_CTX = None  # set by register(); CLI handler runs without a ctx argument.


def register(ctx):
    global _CTX
    _CTX = ctx
    ctx.register_hook("transform_tool_result", on_transform_tool_result)


# ---------------------------------------------------------------- settings

_DEFAULTS = {
    "mode": "off",
    "oversize_tokens": 2000,
    "compression_ratio": 0.4,
    "timeout_s": 30.0,
    "morph_base_url": "https://api.morphllm.com/v1",
    "skip_tools": "",
    "protect_patterns": "ERROR|Error|error|FAIL|Exception|Traceback|OOMKilled|TODO|FIXME|SECRET|PASSWORD|API_KEY",
    "log_enabled": "true",
    "log_max_bytes": 5242880,
    "log_keep_files": 10,
    "log_retention_days": 30,
}


def _from_ctx(ctx, key, default):
    if ctx is None:
        return os.environ.get("CTX_PRUNE_" + key.upper(), default)
    try:
        cfg = ctx.config("ctx-prune") if hasattr(ctx, "config") else None
    except Exception:
        cfg = None
    if isinstance(cfg, dict) and key in cfg:
        return cfg[key]
    return os.environ.get("CTX_PRUNE_" + key.upper(), default)


def _num_from_ctx(ctx, key, default, cast):
    try:
        return cast(_from_ctx(ctx, key, default))
    except (TypeError, ValueError):
        return default


def _settings(ctx=None):
    c = ctx if ctx is not None else _CTX
    skip = str(_from_ctx(c, "skip_tools", "") or "")
    return {
        "mode": str(_from_ctx(c, "mode", "off") or "off").lower(),
        "oversize_tokens": _num_from_ctx(c, "oversize_tokens", 2000, int),
        "compression_ratio": _num_from_ctx(c, "compression_ratio", 0.4, float),
        "timeout_s": _num_from_ctx(c, "timeout_s", 30.0, float),
        "morph_base_url": str(_from_ctx(c, "morph_base_url", _DEFAULTS["morph_base_url"]) or _DEFAULTS["morph_base_url"]).rstrip("/"),
        "skip_tools": {t.strip().lower() for t in skip.split(",") if t.strip()},
        "protect_patterns": str(_from_ctx(c, "protect_patterns", _DEFAULTS["protect_patterns"]) or ""),
        "log_enabled": _from_ctx(c, "log_enabled", "true"),
        "log_max_bytes": _num_from_ctx(c, "log_max_bytes", 5242880, int),
        "log_keep_files": _num_from_ctx(c, "log_keep_files", 10, int),
        "log_retention_days": _num_from_ctx(c, "log_retention_days", 30, int),
    }


# ---------------------------------------------------------------- logging

def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _events_path() -> Path:
    return _hermes_home() / "logs" / "ctx-prune-events.jsonl"


def _log_enabled(settings) -> bool:
    v = settings.get("log_enabled", "true")
    return str(v).lower() not in ("0", "false", "no", "off", "")


def _prune_logs(directory: Path, settings) -> None:
    try:
        days = int(settings.get("log_retention_days", 30) or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return
    cutoff = time.time() - days * 86400
    for child in directory.glob("ctx-prune-events.jsonl.*"):
        try:
            if child.stat().st_mtime < cutoff:
                child.unlink()
        except OSError:
            pass


def _rotate_if_needed(p: Path, settings) -> None:
    try:
        max_bytes = int(settings.get("log_max_bytes", 5242880) or 0)
    except (TypeError, ValueError):
        max_bytes = 0
    if max_bytes <= 0 or not p.exists() or p.stat().st_size < max_bytes:
        return
    keep = int(settings.get("log_keep_files", 10) or 0)
    for i in range(keep - 1, 0, -1) if keep > 0 else []:
        src, dst = p.with_suffix(f".jsonl.{i}"), p.with_suffix(f".jsonl.{i + 1}")
        if src.exists():
            try:
                src.rename(dst)
            except OSError:
                pass
    try:
        p.rename(p.with_suffix(".jsonl.1"))
    except OSError:
        pass
    if keep == 0:
        _prune_logs(p.parent, settings)


def _log_event(ev: dict, settings) -> None:
    if not _log_enabled(settings):
        return
    try:
        p = _events_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(p, settings)
        ev = dict(ev)
        ev.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()))
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- keepContext

def wrap_protected_lines(text: str, pattern: str) -> tuple[str, int]:
    """Wrap lines matching pattern in <keepContext> tags (own line, same message).

    Contiguous matching lines share one tag pair. Tags are stripped from Morph
    output; wrapped content survives verbatim and counts against the ratio budget.
    Returns (wrapped_text, protected_line_count).
    """
    if not pattern:
        return text, 0
    try:
        rx = re.compile(pattern)
    except re.error:
        logger.warning("ctx-prune: bad protect_patterns regex, ignoring")
        return text, 0
    out, in_block, kept = [], False, 0
    for line in text.splitlines():
        hit = bool(rx.search(line))
        if hit and not in_block:
            out.append("<keepContext>")
            in_block = True
        if not hit and in_block:
            out.append("</keepContext>")
            in_block = False
        if hit:
            kept += 1
        out.append(line)
    if in_block:
        out.append("</keepContext>")
    return "\n".join(out), kept


# ---------------------------------------------------------------- morph client

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _morph_key() -> str:
    return os.environ.get("MORPH_API_KEY", "")


def morph_compact(text: str, query: str | None, settings) -> str | None:
    """Compact text via native POST /v1/compact. Returns compacted text or None."""
    key = _morph_key()
    if not key:
        return None
    guarded, kept = wrap_protected_lines(text, settings.get("protect_patterns", ""))
    body = {
        "input": guarded,
        "compression_ratio": settings["compression_ratio"],
        "preserve_recent": 0,  # single tool output: no "recent messages" concept
        "include_markers": True,
        "include_line_ranges": False,
    }
    if query:
        body["query"] = query
    try:
        req = urllib.request.Request(
            settings["morph_base_url"] + "/compact",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=settings["timeout_s"]) as resp:
            data = json.load(resp)
    except Exception as exc:
        logger.warning("ctx-prune: morph call failed: %s", type(exc).__name__)
        return None
    out = data.get("output")
    if isinstance(out, str) and out.strip():
        return out
    msgs = data.get("messages") or []
    parts = [m.get("content", "") for m in msgs if isinstance(m, dict)]
    joined = "\n".join(p for p in parts if p)
    return joined or None


# ---------------------------------------------------------------- hook

def _disabled(settings, ctx) -> bool:
    if settings["mode"] == "off":
        return True
    if settings["mode"] == "auto" and not _morph_key():
        return True
    return settings["mode"] not in ("auto", "on")


def prune_tool_result(result: str, tool_name: str = "", query: str | None = None,
                       settings=None) -> str | None:
    """Pure core: returns compacted replacement, or None to leave untouched."""
    settings = settings or _settings()
    if _disabled(settings, _CTX):
        return None
    if tool_name.lower() in settings["skip_tools"]:
        return None
    if _estimate_tokens(result) < settings["oversize_tokens"]:
        return None
    return morph_compact(result, query, settings)


def on_transform_tool_result(**kwargs):
    """Hook entry. Return a replacement string, or None to keep the original."""
    try:
        st = _settings()
        result = kwargs.get("result")
        if not isinstance(result, str) or not result.strip():
            return None
        tool_name = str(kwargs.get("tool_name") or "")
        query = kwargs.get("user_message") or kwargs.get("goal")
        query = str(query)[:500] if query else None
        t0 = time.time()
        replacement = prune_tool_result(result, tool_name, query, st)
        dt = time.time() - t0
        _, protected = wrap_protected_lines(result, st.get("protect_patterns", ""))
        _log_event({
            "event": "prune",
            "session_id": str(kwargs.get("session_id") or ""),
            "turn_id": str(kwargs.get("turn_id") or ""),
            "tool": tool_name,
            "in_chars": len(result),
            "pruned": replacement is not None,
            "out_chars": len(replacement) if replacement else 0,
            "protected_lines": protected,
            "duration_s": round(dt, 3),
        }, st)
        return replacement  # None = untouched (fail-open)
    except Exception as exc:
        logger.warning("ctx-prune: hook failed: %s", type(exc).__name__)
        return None
