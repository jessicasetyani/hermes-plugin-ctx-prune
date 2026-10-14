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
```

Key comes from shell env (`MORPH_API_KEY` in `~/.zshrc`) — never in config.

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
