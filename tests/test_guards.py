"""Offline tests for ctx-prune Layers 0-3 (guards). No network: HTTP is stubbed.

Regression cover for the 14 Oct 2026 live incident: seven tool results — including
a 666,879-char OpenRouter model catalog — were replaced by Morph's 32-byte
`{"result": "(filtered 1 lines)"}` marker and recorded as successful prunes.
Root cause: those payloads were minified JSON, i.e. ONE physical line, and Morph
Compact removes whole lines only (docs.morphllm.com/sdk/components/compact).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Keep test artifacts (anomaly captures) out of the real ~/.hermes.
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="ctx-prune-guards-")
os.environ.setdefault("MORPH_API_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import __init__ as ctx_prune  # noqa: E402


def _st(**over):
    s = dict(ctx_prune._DEFAULTS)
    s.update(mode="on", oversize_tokens=2000, compression_ratio=0.4,
             timeout_s=5.0, skip_tools=set(), log_enabled="false")
    s.update(over)
    return s


class _Fake:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(output, usage=None, calls=None):
    """Install a fake /v1/compact transport; optionally record what was sent."""
    def _urlopen(req, timeout=None):
        if calls is not None:
            body = json.loads(req.data.decode())
            calls.append({"chars": len(body["input"]),
                          "lines": len(body["input"].splitlines())})
        return _Fake({"output": output, "usage": usage or {}})
    ctx_prune.urllib.request.urlopen = _urlopen


# The exact 32-char marker that wiped the live payloads.
STUB = '{"result": "(filtered 1 lines)"}'
# The seven real collapses from 14 Oct 2026 (input chars).
WIPE_SIZES = [15794, 666879, 91730, 89683, 17059, 12238, 29780]


# ------------------------------------------------------------------ Layer 0

def test_single_line_json_is_de_minified_losslessly():
    payload = json.dumps({"data": {"issues": [{"key": f"IDP-{i}"} for i in range(500)]}})
    assert "\n" not in payload, "fixture must be one physical line"
    prepared = ctx_prune._prepare_input(payload, _st())
    assert prepared is not None
    assert len(prepared.splitlines()) > 500, "Morph needs lines to delete"
    assert json.loads(prepared) == json.loads(payload), "de-minify must be lossless"


def test_single_line_non_json_is_skipped():
    assert ctx_prune._prepare_input("Z" * 9000, _st()) is None


def test_multi_line_payload_passes_through_unchanged():
    text = "line\n" * 3000
    assert ctx_prune._prepare_input(text, _st()) == text


def test_line_starved_payload_never_reaches_the_api():
    calls = []
    _stub(STUB, calls=calls)
    assert ctx_prune.prune_tool_result("Z" * 9000, "terminal", "q", _st()) is None
    assert calls == [], "a line-starved non-JSON payload must never be sent"
    assert ctx_prune._LAST_GUARD_REASON == "uncompactable_single_line"


# ------------------------------------------------------------------ Layer 1

def test_floor_refuses_all_seven_live_collapses():
    for in_chars in WIPE_SIZES:
        assert ctx_prune._guard_reason(STUB, "x" * in_chars, _st()) == "below_keep_floor", \
            f"{in_chars}-char input collapsed to the marker must be refused"


def test_floor_is_relative_not_absolute():
    assert ctx_prune._guard_reason("y" * 100, "x" * 500000, _st()) == "below_keep_floor"


def test_absolute_floor_applies_to_small_inputs():
    assert ctx_prune._guard_reason("z" * 100, "x" * 800, _st(oversize_tokens=1)) == "below_keep_floor"


def test_legitimate_ratio_passes_the_floor():
    assert ctx_prune._guard_reason("y" * 4000, "x" * 10000, _st()) is None


# ------------------------------------------------------------------ Layer 2

def test_marker_only_output_is_refused():
    # Ratio passes (markers are long enough), substance does not.
    markers_only = "(filtered 1 lines)\n" * 60
    assert ctx_prune._guard_reason(markers_only, "x" * 8000, _st()) == "marker_only"


def test_markers_embedded_in_real_content_are_accepted():
    out = "alpha beta gamma\n(filtered 1 lines)\n" + "delta epsilon zeta\n" * 50
    assert ctx_prune._guard_reason(out, "x" * 4000, _st()) is None


def test_substance_ignores_whitespace_and_keepcontext_tags():
    assert ctx_prune._has_substance(
        "<keepContext>\n(filtered 3 lines)\n</keepContext>", 1) is False


# --------------------------------------------- end-to-end through the core

def test_collapse_keeps_the_original_and_sends_the_de_minified_form():
    inner = json.dumps({"data": {"issues": [{"key": f"IDP-{i}"} for i in range(9000)]}})
    payload = json.dumps({"result": inner})
    calls = []
    _stub(STUB, usage={"input_tokens": 179994, "output_tokens": 5}, calls=calls)
    assert ctx_prune.prune_tool_result(payload, "mcp__x", "q", _st()) is None
    assert ctx_prune._LAST_GUARD_REASON == "below_keep_floor"
    assert calls and calls[0]["lines"] > 1000, "the de-minified form is what gets sent"


def test_real_compaction_still_applies():
    text = "def f():\n    return 1\n\n"
    _stub(text * 200)
    assert ctx_prune.prune_tool_result(text * 900, "terminal", "q", _st()) is not None


def test_guard_settings_are_declared():
    st = ctx_prune._settings(None)
    for key in ("min_keep_ratio", "min_keep_tokens", "min_lines", "marker_min_chars"):
        assert key in st, f"{key} missing from settings"
    assert ctx_prune._DEFAULTS["min_keep_ratio"] == 0.10
    assert ctx_prune._DEFAULTS["min_keep_tokens"] == 200


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        fn()
        print("PASS", name)
