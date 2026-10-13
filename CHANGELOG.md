# Changelog

## 0.1.0 — 13 Oct 2026
- Initial: `transform_tool_result` observer hook (Layer 1), native `POST /v1/compact`
- `mode: off|auto|on`, `oversize_tokens`, `compression_ratio`, `skip_tools`, rotating event log
- 5 offline tests pass; live probe: 59% reduction, query-relevant line verbatim
- Layer 2 (`llm_request` middleware for history pruning) not yet built
