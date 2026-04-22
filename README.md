# claude-tokens-monitor

Lightweight token-usage counter for [Claude Code](https://claude.com/claude-code) — shows your **current session** and **weekly** quota in real time, both in the terminal and in the macOS menu bar via [SwiftBar](https://swiftbar.app/).

Mirrors what you see at <https://claude.ai/settings/usage> but works offline (reads `~/.claude/projects/*/*.jsonl`) and queries the same Anthropic OAuth `usage` endpoint that Claude Code itself uses for accurate utilization percentages.

## Why?

Claude Code does not expose a programmatic way to monitor your remaining session/weekly tokens. The official `claude.ai/settings/usage` page is a manual click. This script gives you:

- **Single command** to print a full report from the terminal.
- **Live mode** (`--live`) that refreshes every 10 seconds.
- **SwiftBar mode** (`--swiftbar`) for an always-visible menu-bar item on macOS.
- **API mode** (default on macOS): pulls real percentages from Anthropic's `oauth/usage` endpoint using the OAuth token from your Keychain — no setup, no API key needed.
- **Offline fallback** (`--no-api`): counts tokens locally from your `*.jsonl` history.
- **Calibration**: once you know your real quota (from `claude.ai/settings/usage`), tune the script to match precisely.

## Requirements

- **Python 3.9+** (uses only the standard library — no `pip install` needed).
- **macOS** for the API mode (uses `security` to read the OAuth token from Keychain). Linux/WSL: works in `--no-api` mode by reading `~/.claude/projects/*/*.jsonl`.
- **Claude Code** installed and signed in (so `~/.claude/projects/` and the Keychain entry exist).
- *(Optional)* **[SwiftBar](https://swiftbar.app/)** for the menu-bar plugin.

## Install

```bash
# Clone or copy the script to a stable location
git clone https://github.com/<your-username>/claude-tokens-monitor.git ~/.local/share/claude-tokens
chmod +x ~/.local/share/claude-tokens/claude_tokens.py
```

Or just download `claude_tokens.py` and place it anywhere convenient.

## First-run setup

```bash
python3 claude_tokens.py --setup
```

Interactive prompt asks two questions:

1. **Your Claude plan** — Pro / Max 5x / Max 20x. The script uses this to set default quota numbers.
2. **Your timezone (UTC offset)** — used to align the weekly reset display with `claude.ai`, which shows reset in your local time.

Settings are saved to `~/.config/claude-tokens/config.json`. You can re-run `--setup` any time to change them, or override per-run via env vars `CLAUDE_PLAN` / `CLAUDE_TZ_OFFSET_H`.

## Usage

```bash
# One-shot report
python3 claude_tokens.py

# Live mode, refresh every 10 s
python3 claude_tokens.py --live

# SwiftBar single-line output (used by the .sh wrapper)
python3 claude_tokens.py --swiftbar

# Skip the Anthropic API and count tokens from local JSONL only
python3 claude_tokens.py --no-api
```

### Sample output

```
═══ Claude Code Usage · plan max5 · api ═══

Current session (09:15 → +5h):
  Used:          36.0%   1.7M / 4.7M
  Resets in:     4h 03m

Weekly · All models (resets Sat 23:00):
  Used:          90.0%   118.3M / 131.5M
  Resets in:     3d 13h

Weekly · Sonnet only (resets Sun 00:00):
  Used:          11.0%   12.7M / 115.6M
  Resets in:     3d 14h

By model (session):
  opus-4-7                  4.1M  (88.0%)
  sonnet-4-6                85.7K  (1.8%)
```

(The script's actual UI strings are in Russian; this is an illustrative translation. Localising the strings is a planned PR.)

## SwiftBar plugin

1. Install [SwiftBar](https://swiftbar.app/) and point it at a plugins folder (default `~/Library/Application Support/SwiftBar/Plugins/`).
2. Copy the wrapper script `claude_api.30s.sh` into that folder. Edit one line — the path to `claude_tokens.py`.
3. The menu bar will show `⚡ {session %} · {tokens} · {time to reset}`. Click for a detailed breakdown.

## Calibration (optional, recommended)

The default quotas in `PLAN_QUOTAS` are estimates. Once your dashboard shows you a non-trivial percentage (~5%+ on session or weekly), you can pin the script to your exact quota:

```bash
# Tell the script: "site shows 13% used right now for the session"
python3 claude_tokens.py --calibrate-session 13

# Same for weekly (all models / Sonnet only)
python3 claude_tokens.py --calibrate-weekly 17
python3 claude_tokens.py --calibrate-sonnet 8

# Adjust the session reset timer (site shows 4:34 left, but script shows different)
python3 claude_tokens.py --calibrate-timer 4:34
```

Calibration is stored in `~/.claude/usage_calibration.json` and overrides `PLAN_QUOTAS`.

## How it works

| Mode | Source | Notes |
|------|--------|-------|
| **API** (default) | `https://api.anthropic.com/api/oauth/usage` with the OAuth token from `security find-generic-password -s "Claude Code-credentials"` | Returns the same `utilization` % as `claude.ai/settings/usage`. Cached for 120 s to avoid 429. |
| **Local fallback** (`--no-api` or API failure) | `~/.claude/projects/*/*.jsonl` — Claude Code's transcript history | Counts effective tokens (`input + cache_creation + output`). Less accurate (no server-side aggregation) but works offline. |

The session window is 5 hours, starting from the first request after either:
- A `<synthetic>` "hit your limit" event (forces a new session), or
- A pause longer than 5 hours between requests.

This matches how `ccusage` and `claude.ai` define a session.

## Files & paths

| Path | Purpose |
|------|---------|
| `claude_tokens.py` | The script |
| `~/.config/claude-tokens/config.json` | Your plan + timezone (created by `--setup`) |
| `~/.claude/usage_calibration.json` | Optional fine-tuned quotas (created by `--calibrate-*`) |
| `~/.claude/usage_api_cache.json` | API response cache (auto, 120 s TTL) |
| `~/.claude/projects/*/*.jsonl` | Claude Code transcript history (read-only) |

## Privacy & security

- The script only reads the OAuth token from your local Keychain — it never writes it anywhere.
- The OAuth token is sent in a single HTTPS request to `api.anthropic.com` (the same endpoint Claude Code uses internally).
- No telemetry, no third-party services.
- Your transcripts (`~/.claude/projects/*/*.jsonl`) are read line-by-line for token counts only — content is never logged or transmitted.

## Limitations

- API mode requires a recent Claude Code install (the `Claude Code-credentials` entry must exist in your Keychain).
- The Anthropic `oauth/usage` endpoint is undocumented and may change at any time. The local-fallback mode protects against this.
- Token quota estimates per plan are community-derived. Use `--calibrate-*` for exact numbers.

## Contributing

PRs welcome — especially:
- Localising the UI to English / Spanish / etc. (currently Russian).
- A Linux/Windows alternative for the API mode (Keychain-equivalent).
- Native menu bar plugins for non-SwiftBar users (xbar, BitBar).

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Inspired by [ccusage](https://github.com/ryoppippi/ccusage) and the way `claude.ai` displays the same data.
