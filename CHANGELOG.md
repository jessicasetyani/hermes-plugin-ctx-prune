# Changelog

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
