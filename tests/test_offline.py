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
