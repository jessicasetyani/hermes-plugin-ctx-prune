# Changelog

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
