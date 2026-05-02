#!/usr/bin/env bash
# agent-costs hook — called by post_tool_call to auto-track costs
# Writes aggregated stats to ~/.hermes/agent-costs/session.json after each API call.
# Run by Hermes shell hook or called manually.

set -euo pipefail

INPUT=$(cat 2>/dev/null || echo "{}")
EVENT=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null || echo "")

# Only run for post_llm_call events (after API response)
if [ "$EVENT" != "post_llm_call" ] && [ "$EVENT" != "post_tool_call" ]; then
  exit 0
fi

# Just run the tracker in background (non-blocking)
/usr/local/bin/agent-costs --days 1 --json > /dev/null 2>&1 &

exit 0
