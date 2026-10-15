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
    "min_keep_ratio": 0.10,
    "min_keep_tokens": 200,
    "min_lines": 8,
    "marker_min_chars": 120,
}


def _from_ctx(ctx, key, default):
    """Read one plugin setting. Mirrors jev-suggest: ctx.get_config(key, default)."""
    if ctx is None:
        return os.environ.get("CTX_PRUNE_" + key.upper(), default)
    try:
        val = ctx.get_config(key, default)
        if val is not None and val != "":
            return val
    except Exception:
        pass
    # Fallback: some wiring exposes the whole settings dict instead.
    try:
        cfg = ctx.config("ctx-prune") if hasattr(ctx, "config") else None
        if isinstance(cfg, dict) and key in cfg:
            return cfg[key]
    except Exception:
        pass
    return os.environ.get("CTX_PRUNE_" + key.upper(), default)


def _num_from_ctx(ctx, key, default, cast):
    try:
        return cast(_from_ctx(ctx, key, default))
    except (TypeError, ValueError):
        return default


def _normalize_mode(raw) -> str:
    """Coercion-proof: YAML turns unquoted on/off/yes/no into booleans."""
    if isinstance(raw, bool):
        return "on" if raw else "off"
    s = str(raw or "").strip().lower()
    if s in ("on", "true", "yes", "1", "enabled"):
        return "on"
    if s in ("auto",):
        return "auto"
    return "off"


def _settings(ctx=None):
    c = ctx if ctx is not None else _CTX
    skip = str(_from_ctx(c, "skip_tools", "") or "")
    return {
        "mode": _normalize_mode(_from_ctx(c, "mode", "off")),
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
        "min_keep_ratio": _num_from_ctx(c, "min_keep_ratio", 0.10, float),
        "max_keep_ratio": _num_from_ctx(c, "max_keep_ratio", 0.90, float),
        "min_keep_tokens": _num_from_ctx(c, "min_keep_tokens", 200, int),
        "min_lines": _num_from_ctx(c, "min_lines", 8, int),
        "marker_min_chars": _num_from_ctx(c, "marker_min_chars", 120, int),
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


def _unwrap_envelope(result: str) -> dict | None:
    """Return the tool-result envelope as a dict, or None when it is plain text.

    Hermes hands tool results to hooks JSON-serialised, e.g.
    ``{"output": "line1\\nline2..."}`` — ONE physical line with escaped \\n.
    Morph deletes whole lines, so a single-line payload cannot be compacted
    ("results will be poor" / input returned unchanged). Unwrap first.
    """
    stripped = result.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------- guards

# Provider bookkeeping emitted by Morph Compact. Anything left after these are
# stripped is real content; if nothing is left, the "compaction" was a wipe.
_FILTERED_MARKER = re.compile(r"\((?:filtered|compacted)\s+\d+\s+lines?\)")
_KEEP_TAG = re.compile(r"</?keepContext>")


def _line_starved(text: str, settings) -> bool:
    """True when the payload is too line-poor for line-deletion compaction.

    Morph Compact removes whole LINES — it never trims inside a line. A
    minified JSON blob is one physical line, so there is nothing to delete
    selectively and the entire payload is dropped. Documented in
    docs.morphllm.com/sdk/components/compact.
    """
    if not text:
        return True
    try:
        min_lines = int(settings.get("min_lines", 8) or 8)
    except (TypeError, ValueError):
        min_lines = 8
    return len(text.splitlines()) < min_lines


def _prepare_input(text: str, settings):
    """Layer 0: return a compactable form of text, or None to skip pruning.

    Minified JSON is re-serialised with newlines (lossless: identical data,
    far more lines) so Morph has something to delete. Any other line-starved
    payload is skipped — fail-open cannot lose content, sending it can.
    """
    if not _line_starved(text, settings):
        return text
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    try:
        # indent=0 = one JSON element per line, no padding: identical line
        # count to indent=1 at ~1/5 of the size growth (Morph bills input).
        return json.dumps(obj, indent=0, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _strip_markers(text: str) -> str:
    """Remove the provider's own bookkeeping so only real content remains."""
    return _KEEP_TAG.sub("", _FILTERED_MARKER.sub("", text))


def _has_substance(out: str, min_chars: int) -> bool:
    """Layer 2: the output must carry real content, not only markers."""
    return len("".join(_strip_markers(out).split())) >= max(1, int(min_chars))


def _count_filtered(text: str) -> int:
    """Layer 3: total of the provider's own '(filtered N lines)' accounting."""
    total = 0
    for m in _FILTERED_MARKER.finditer(text):
        digits = re.search(r"\d+", m.group(0))
        if digits:
            total += int(digits.group(0))
    return total


def _guard_reason(out: str, in_text: str, settings) -> str | None:
    """Layers 1-2: why this compaction must be refused, or None if it is OK.

    Layer 1 floor — a replacement that keeps almost none of the input is a
    content wipe wearing the costume of a successful prune.
    Layer 2 substance — an output that is only provider markers carries no
    information even when a ratio check on a small input would pass.
    """
    in_tok = _estimate_tokens(in_text)
    out_tok = _estimate_tokens(out)
    try:
        ratio = float(settings.get("min_keep_ratio", 0.10) or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    try:
        floor_tokens = int(settings.get("min_keep_tokens", 200) or 0)
    except (TypeError, ValueError):
        floor_tokens = 0
    if out_tok < max(floor_tokens, int(ratio * in_tok)):
        return "below_keep_floor"
    try:
        max_keep = float(settings.get("max_keep_ratio", 0.90) or 0.0)
    except (TypeError, ValueError):
        max_keep = 0.0
    if max_keep > 0 and in_tok > 0 and out_tok >= int(max_keep * in_tok):
        return "marginal_gain"
    try:
        min_chars = int(settings.get("marker_min_chars", 120) or 120)
    except (TypeError, ValueError):
        min_chars = 120
    if not _has_substance(out, min_chars):
        return "marker_only"
    return None


def _morph_key() -> str:
    """MORPH_API_KEY via Hermes's credential routes only (catalog rule 11).

    Order: agent.secret_scope (profile-aware, sanctioned route), then the
    declared env var. Never reads .env files or any credential store directly.
    """
    try:
        from agent.secret_scope import get_secret  # type: ignore
    except (ImportError, ModuleNotFoundError):
        get_secret = None
    if get_secret is not None:
        try:
            key = str(get_secret("MORPH_API_KEY", "") or "").strip()
            if key:
                return key
        except Exception as exc:
            logger.debug("ctx-prune: secret_scope unavailable: %s", exc)
    # Declared in requires_env; honored by Hermes's env injection.
    return os.environ.get("MORPH_API_KEY", "").strip()


_LAST_MORPH_USAGE: dict = {}
_LAST_GUARD_REASON: str | None = None


def _dump_anomaly(text: str, out: str) -> None:
    """Capture the exact payload pair so an anomaly is diagnosed from
    evidence instead of guesswork (bounded, one file per occurrence)."""
    try:
        d = _hermes_home() / "logs" / "ctx-prune-anomaly"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
        (d / f"anomaly-{stamp}-in.txt").write_text(text, encoding="utf-8")
        (d / f"anomaly-{stamp}-out.txt").write_text(out, encoding="utf-8")
    except OSError:
        pass


def _reject(reason: str):
    """Record why a compaction was refused, then fail open (return None)."""
    global _LAST_GUARD_REASON
    _LAST_GUARD_REASON = reason
    return None


def morph_compact(text: str, query: str | None, settings) -> str | None:
    """Compact text via native POST /v1/compact. Returns compacted text or None."""
    key = _morph_key()
    if not key:
        return None
    # Layer 0: only line-rich payloads are compactable (Morph deletes whole
    # lines). Minified JSON is re-serialised with newlines; any other
    # line-starved payload is skipped rather than sent to be wiped.
    prepared = _prepare_input(text, settings)
    if prepared is None:
        logger.info("ctx-prune: line-starved non-JSON payload (%d chars), "
                    "skipping prune", len(text))
        return _reject("uncompactable_single_line")
    guarded, kept = wrap_protected_lines(prepared, settings.get("protect_patterns", ""))
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
    # Record the provider's own accounting so the log proves what happened.
    try:
        _LAST_MORPH_USAGE.clear()
        usage = data.get("usage") or {}
        _LAST_MORPH_USAGE.update({
            "morph_in_tokens": usage.get("input_tokens"),
            "morph_out_tokens": usage.get("output_tokens"),
            "morph_ratio": usage.get("compression_ratio"),
            "morph_ms": usage.get("processing_time_ms"),
        })
    except Exception:
        pass
    out = data.get("output")
    if not (isinstance(out, str) and out.strip()):
        msgs = data.get("messages") or []
        parts = [m.get("content", "") for m in msgs if isinstance(m, dict)]
        joined = "\n".join(p for p in parts if p)
        out = joined or None
    if out is None:
        return None
    # Guard: never report a prune that did not actually shrink the payload.
    # Compared against the ORIGINAL text, not the de-minified `prepared` form:
    # the replacement must be smaller than what the model would have received.
    if len(out) >= len(text):
        logger.info("ctx-prune: no reduction (%d -> %d chars), keeping original",
                    len(text), len(out))
        _dump_anomaly(text, out)
        return None
    # Layers 1-2: shorter is not the same as good.
    reason = _guard_reason(out, text, settings)
    if reason is not None:
        logger.warning("ctx-prune: refused compaction (%s) %d -> %d chars, "
                       "keeping original", reason, len(text), len(out))
        _dump_anomaly(text, out)
        return _reject(reason)
    return out


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
    global _LAST_GUARD_REASON
    settings = settings or _settings()
    if _disabled(settings, _CTX):
        return None
    if tool_name.lower() in settings["skip_tools"]:
        return None

    _LAST_GUARD_REASON = None
    envelope = _unwrap_envelope(result)
    if envelope is not None:
        # Envelope path: compact each long text field, keep every other key.
        threshold = settings["oversize_tokens"]
        targets = [k for k, v in envelope.items()
                   if isinstance(v, str) and _estimate_tokens(v) >= threshold]
        if not targets:
            return _reject("no_oversize_field")
        compacted = dict(envelope)
        shrunk = False
        for key in targets:
            new_val = morph_compact(envelope[key], query, settings)
            if new_val is not None:
                compacted[key] = new_val
                shrunk = True
        if not shrunk:
            return None  # every field refused: fail open, reason already set
        rebuilt = json.dumps(compacted, ensure_ascii=False)
        return rebuilt if len(rebuilt) < len(result) else _reject("no_reduction")

    if _estimate_tokens(result) < settings["oversize_tokens"]:
        return None
    return morph_compact(result, query, settings)


def _skip_reason(result: str, tool_name: str, settings) -> str | None:
    """Why this result was left alone — for the event log."""
    if settings.get("mode") == "off":
        return "mode_off"
    if settings.get("mode") not in ("on", "auto"):
        return "mode_invalid"
    if settings.get("mode") == "auto" and not _morph_key():
        return "no_key"
    if tool_name.lower() in settings.get("skip_tools", set()):
        return "skip_tool"
    if _estimate_tokens(result) < settings.get("oversize_tokens", 2000):
        return "below_threshold"
    return None


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
        _LAST_MORPH_USAGE.clear()  # never leak a previous call's usage into this event
        t0 = time.time()
        replacement = prune_tool_result(result, tool_name, query, st)
        dt = time.time() - t0
        _, protected = wrap_protected_lines(result, st.get("protect_patterns", ""))
        reason = None if replacement is not None else (
            _skip_reason(result, tool_name, st)
            or _LAST_GUARD_REASON
            or "morph_returned_none"
        )
        _log_event({
            "event": "prune",
            "session_id": str(kwargs.get("session_id") or ""),
            "turn_id": str(kwargs.get("turn_id") or ""),
            "tool": tool_name,
            "mode": st.get("mode"),
            "in_chars": len(result),
            "in_tokens_est": _estimate_tokens(result),
            "pruned": replacement is not None,
            "reason": reason,
            "out_chars": len(replacement) if replacement else 0,
            "protected_lines": protected,
            "duration_s": round(dt, 3),
            "lines_in": len(result.splitlines()),
            "lines_out": len(replacement.splitlines()) if replacement else 0,
            "lines_filtered": _count_filtered(replacement) if replacement else 0,
            **_LAST_MORPH_USAGE,
        }, st)
        return replacement  # None = untouched (fail-open)
    except Exception as exc:
        logger.warning("ctx-prune: hook failed: %s", type(exc).__name__)
        return None
