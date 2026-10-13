"""Offline tests for ctx-prune Layer 1. No network: morph_compact is stubbed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import __init__ as ctx_prune  # noqa: E402


def _st(**over):
    s = dict(ctx_prune._DEFAULTS)
    s.update(mode="on", oversize_tokens=2000, compression_ratio=0.4,
             timeout_s=5.0, skip_tools=set(), log_enabled="false")
    s.update(over)
    return s


def test_small_result_untouched():
    out = ctx_prune.prune_tool_result("ok", "terminal", None, _st())
    assert out is None, "small results must pass through"


def test_mode_off_disables():
    big = "x" * 20000
    out = ctx_prune.prune_tool_result(big, "terminal", None, _st(mode="off"))
    assert out is None, "mode off must disable"


def test_skip_tools():
    big = "x" * 20000
    out = ctx_prune.prune_tool_result(big, "clarify", None, _st(skip_tools={"clarify"}))
    assert out is None, "skipped tools must pass through"


def test_oversize_calls_morph_and_returns_replacement():
    big = "line one\nline two\n" * 1000
    calls = {}

    def fake_compact(text, query, settings):
        calls["n"] = calls.get("n", 0) + 1
        return "COMPACTED"

    orig = ctx_prune.morph_compact
    ctx_prune.morph_compact = fake_compact
    try:
        out = ctx_prune.prune_tool_result(big, "terminal", "some goal", _st())
    finally:
        ctx_prune.morph_compact = orig
    assert out == "COMPACTED" and calls.get("n") == 1


def test_hook_fail_open_on_exception():
    orig = ctx_prune.prune_tool_result

    def boom(*a, **k):
        raise RuntimeError("x")

    ctx_prune.prune_tool_result = boom
    try:
        out = ctx_prune.on_transform_tool_result(result="y" * 20000, tool_name="t")
    finally:
        ctx_prune.prune_tool_result = orig
    assert out is None, "hook must fail open"


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        fn()
        print("PASS", name)


def test_wrap_protected_lines():
    text = "ok line\nERROR boom happened\nTraceback detail\nok again"
    wrapped, kept = ctx_prune.wrap_protected_lines(text, "ERROR|Traceback")
    assert kept == 2
    assert "<keepContext>\nERROR boom happened\nTraceback detail\n</keepContext>" in wrapped
    assert "ok line\n" in wrapped and wrapped.endswith("ok again")


def test_wrap_bad_regex_fails_open():
    wrapped, kept = ctx_prune.wrap_protected_lines("some text", "([invalid")
    assert (wrapped, kept) == ("some text", 0)


def test_wrap_empty_pattern_noop():
    wrapped, kept = ctx_prune.wrap_protected_lines("a\nb", "")
    assert (wrapped, kept) == ("a\nb", 0)


def test_normalize_mode_coercion():
    n = ctx_prune._normalize_mode
    assert n(True) == "on" and n(False) == "off"
    assert n("on") == "on" and n("OFF") == "off"
    assert n("auto") == "auto" and n("yes") == "on"
    assert n("") == "off" and n(None) == "off"


class _FakeCtx:
    """Mimics the real plugin ctx: get_config(key, default)."""

    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self, key, default=None):
        return self._cfg.get(key, default)


def test_get_config_path_enables_mode():
    ctx = _FakeCtx({"mode": True, "log_enabled": False})
    st = ctx_prune._settings(ctx)
    assert st["mode"] == "on", st["mode"]
    assert ctx_prune._disabled(st, ctx) is False


def test_mode_absent_defaults_off():
    ctx = _FakeCtx({})
    assert ctx_prune._settings(ctx)["mode"] == "off"
