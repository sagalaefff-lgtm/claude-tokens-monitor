#!/bin/bash
# SwiftBar plugin wrapper for claude-tokens-monitor.
# Refresh every 30 seconds (the .30s. in the filename is parsed by SwiftBar).
#
# Setup:
#   1. Install SwiftBar:                  https://swiftbar.app/
#   2. Copy this file to your plugins dir, e.g.:
#        ~/Library/Application Support/SwiftBar/Plugins/claude_api.30s.sh
#   3. Edit SCRIPT_PATH below to point to your installed claude_tokens.py.
#   4. Make this file executable:         chmod +x claude_api.30s.sh
#   5. SwiftBar will pick it up automatically (or click Refresh in the menu).
#
# Tip: change "30s" in the filename to "10s" for 10-second refresh, "1m" for once a minute, etc.

# ─── Configure this path to where you installed claude_tokens.py ──────────────
SCRIPT_PATH="$HOME/.local/share/claude-tokens/claude_tokens.py"
# ──────────────────────────────────────────────────────────────────────────────

# Use the user's PATH so /usr/bin/python3 (or pyenv) is found
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "⚠ claude_tokens.py not found"
    echo "---"
    echo "Edit SCRIPT_PATH in this wrapper file:"
    echo "$0 | shell=open"
    exit 0
fi

exec /usr/bin/python3 "$SCRIPT_PATH" --swiftbar
