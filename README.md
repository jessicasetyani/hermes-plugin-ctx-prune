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

## Install

```bash
hermes plugins install jessicasetyani/hermes-plugin-ctx-prune --enable
```

This is the only supported install path — it records git provenance with a
pinned commit, which is what makes updates work. Do NOT `cp -r` the repo into
`~/.hermes/plugins/`: a manual copy has provenance `manual` and can never be
updated.

After install: add `MORPH_API_KEY` to your shell env (e.g. `~/.zshrc`), set the
`plugins.ctx-prune` block in `config.yaml` (see Config), then `/reset` —
plugins take effect on the next session, never mid-conversation.

Updates:

```bash
hermes plugins check-updates    # poll for new commits
hermes plugins update ctx-prune # pull, re-validate, then /reset
```

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
    min_lines: 8          # below this line count = line-starved (see Guards)
    min_keep_ratio: 0.10  # refuse a compaction keeping < this fraction of input tokens
    min_keep_tokens: 200  # absolute floor, in tokens
    marker_min_chars: 120 # refuse output that is only provider markers
```

Key comes from shell env (`MORPH_API_KEY` in `~/.zshrc`) — never in config.

## Guards

Morph Compact removes whole **lines** — it never trims inside a line
([docs](https://docs.morphllm.com/sdk/components/compact)). A minified JSON
payload is a single physical line, so there is nothing to delete selectively,
Morph drops the whole payload, and all that comes back is its own
`(filtered N lines)` marker. On 14 Oct 2026 that wiped 7 of 66 prunes — including
a 666,879-char catalog response — each replaced by a 32-byte stub that the old
"did it get shorter?" check happily accepted as a success.

Three guards stop that before it reaches the model:

- **Layer 0 — precondition.** Line-starved payloads (fewer than `min_lines` lines)
  are never sent as-is. Single-line JSON is re-serialised with `indent=0` — one
  element per line, lossless, ~+10% chars — so Morph has lines to delete;
  any other line-starved payload is **skipped**. Fail-open beats a wipe.
- **Layer 1 — floor.** A replacement keeping less than
  `max(min_keep_tokens, min_keep_ratio × in_tokens)` is refused
  (`below_keep_floor`).
- **Layer 2 — substance.** Output whose content — after stripping provider
  markers and `<keepContext>` tags — is shorter than `marker_min_chars` is
  refused (`marker_only`).

Every refusal returns `None`, so the original result passes through untouched,
and the in/out pair is dumped to `~/.hermes/logs/ctx-prune-anomaly/`. Each event
records `lines_in`, `lines_out` and `lines_filtered`, so a destructive no-op is
visible in the log instead of being counted as a saving.

## Fails open

Missing key, timeout, HTTP error, any exception → hook returns `None`,
original tool result passes through. Never blocks a turn.

## Disclosure (catalog rule 13)

- **Network calls to a third-party service:** every pruned tool output is sent
  to `api.morphllm.com` (Morph) — content leaves the machine. Tool outputs
  under `oversize_tokens` are never sent. To keep everything local, leave
  `mode` off or point `morph_base_url` at a self-hosted Morph deployment.
- **Reads outside the plugin's own data:** none. The plugin reads only its own
  settings and, for the key, Hermes's own credential route.
- **Shell commands / background processes:** none.
- **Stored credentials:** none. The key is read per call from
  `agent.secret_scope.get_secret("MORPH_API_KEY")`, falling back to the
  declared `requires_env` variable. No key is written anywhere.
- **Telemetry:** none.
