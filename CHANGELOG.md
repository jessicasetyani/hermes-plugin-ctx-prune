# Changelog

## 0.6.0 — 15 Oct 2026
- Fix (root cause found from captured live payloads): Morph Compact deletes whole
  LINES only — it never trims inside a line — so a minified JSON payload (one
  physical line) had nothing to delete selectively and Morph returned its own
  `(filtered N lines)` marker, dropping the entire payload. On 14 Oct this wiped
  7 of 66 prunes, including a 666,879-char OpenRouter catalog and two Jira result
  sets, each replaced by a 32-byte stub that the old guard accepted because it was
  merely *shorter*. Source: docs.morphllm.com/sdk/components/compact.
- Layer 0 (precondition): line-starved payloads are no longer sent as-is. Single-line
  JSON is re-serialised with `indent=0` — one element per line, lossless, ~+10%
  chars — so Morph has lines to delete; any other line-starved payload is skipped
  (fail-open) instead of being sent to be wiped. `min_lines` (default 8).
- Layer 1 (floor): refuse a replacement that keeps less than
  `max(min_keep_tokens, min_keep_ratio x in_tokens)` — the 32-byte stub is now
  rejected as `below_keep_floor`. Defaults: 200 tokens / 0.10.
- Layer 2 (substance): refuse output whose content, after stripping the provider's
  `(filtered N lines)` markers and `<keepContext>` tags, is below `marker_min_chars`
  (default 120) — catches a marker-only wipe that a ratio check could pass.
- Layer 3 (observability): every event now records `lines_in`, `lines_out` and
  `lines_filtered`, so a destructive no-op is visible in the log; refusals dump the
  in/out pair to `~/.hermes/logs/ctx-prune-anomaly/`. New refusal reasons:
  `below_keep_floor`, `marker_only`, `uncompactable_single_line`, `no_oversize_field`.
- Verified: all 7 live collapses refused; 59/59 legitimate prunes from the 14 Oct
  log still accepted (zero false rejects); 6 captured live payloads de-minified
  1 line -> up to 18,458 lines. 27 offline tests pass (13 existing + 14 new).

## 0.5.0 — 13 Oct 2026
- Fix (root cause found from a captured live payload): Hermes hands tool results
  to hooks JSON-serialised — `{"output": "line1\nline2..."}`, one physical line
  with escaped newlines. Morph deletes whole lines, so a single-line payload was
  returned unchanged (ratio 0.999) and never pruned. The envelope is now unwrapped,
  the inner text compacted, and the envelope rebuilt with all other keys intact.
- Verified on the failing live payload: 25,868 -> 10,814 chars (ratio 0.418),
  protected (ERROR) line kept, exit_code preserved.

## 0.4.1 — 13 Oct 2026
- Anomaly capture: when Morph returns a non-shrinking payload, the exact input
  and output are written to `~/.hermes/logs/ctx-prune-anomaly/` for diagnosis

## 0.4.0 — 13 Oct 2026
- Fix: reject any replacement that does not shrink the payload (a "pruned: true" with equal in/out chars is now a no-op with an explicit reason)
- Log Morph's own accounting per event: `morph_in_tokens`, `morph_out_tokens`, `morph_ratio`, `morph_ms`
- Usage no longer leaks between events

## 0.3.0 — 13 Oct 2026
- Fix: settings read via `ctx.get_config(key, default)` — mode was silently "off"
- Event log now records `mode`, `in_tokens_est`, and `reason` (mode_off / below_threshold / no_key / morph_returned_none)
- Key read via `agent.secret_scope.get_secret` with declared-env fallback (catalog rule 11)
- `requires_hermes: ">=0.21.5"`, version synced; `hermes plugins validate` fully green
- README disclosure section (catalog rule 13)

## 0.1.0 — 13 Oct 2026
- Initial: `transform_tool_result` observer hook (Layer 1), native `POST /v1/compact`
- `mode: off|auto|on`, `oversize_tokens`, `compression_ratio`, `skip_tools`, rotating event log
- 5 offline tests pass; live probe: 59% reduction, query-relevant line verbatim
- Layer 2 (`llm_request` middleware for history pruning) not yet built
