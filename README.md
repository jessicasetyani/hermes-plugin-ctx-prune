# ctx-prune

Layer-1 context pruning for Hermes Agent via Morph Compact (`transform_tool_result` observer hook).

## What it does

Oversized tool outputs (default: >2000 estimated tokens) are compacted through
Morph's native `POST /v1/compact` endpoint — with the turn's goal as `query` —
before they enter model context. Small results pass through untouched.

Verified behavior (13 Oct 2026, live `/v1/compact`):
- ~12k-char log dump → ~5k chars (59% reduction), `compression_ratio: 0.4`
- Query-relevant line (`ERROR ... OOMKilled`) survives **byte-for-byte**
- Surviving lines are verbatim; removed ranges marked `(filtered N lines)`

## Why the native endpoint

`/v1/chat/completions` with `model: morph-compactor` silently drops
`preserve_recent` and audit ranges. The native endpoint keeps `query`,
`compression_ratio`, `preserve_recent`, `include_markers`, line ranges.
The aux-compression slot (`auxiliary.compression`) must NOT use this model:
proven 13 Oct 2026 that `morph-compactor` deletes lines instead of writing
Hermes's rewrite-style summary — aux stays on a summarizer model.

## Config (per profile, config.yaml)

```yaml
plugins:
  ctx-prune:
    mode: "off"        # off | auto (on when MORPH_API_KEY present) | on
    oversize_tokens: 2000
    compression_ratio: 0.4
    timeout_s: 30.0
    skip_tools: ""     # e.g. "skill,clarify"
    log_enabled: "true"   # laptop ON, server OFF (11 Oct convention)
```

Key comes from shell env (`MORPH_API_KEY` in `~/.zshrc`) — never in config.

## Fails open

Missing key, timeout, HTTP error, any exception → hook returns `None`,
original tool result passes through. Never blocks a turn.
