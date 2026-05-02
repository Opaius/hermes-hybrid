#!/usr/bin/env bash
# ctx-stats — token savings counter for context-mode usage in Hermes.
# Tracks bytes saved per ctx_execute call and shows cumulative stats.
#
# Part of hermes-ctx-enhance: https://github.com/cioky/hermes-ctx-enhance
#
# Usage: ctx-stats [--reset] [--json]
#   ctx-stats              Show human-readable stats
#   ctx-stats --json       Machine-readable JSON output
#   ctx-stats --reset      Reset all counters

set -euo pipefail

STATS_DIR="$HOME/.hermes/ctx-stats"
STATS_FILE="$STATS_DIR/totals.json"
mkdir -p "$STATS_DIR"

# Initialize if missing
if [ ! -f "$STATS_FILE" ]; then
  echo '{"calls":0,"bytes_saved":0,"calls_blocked":0,"since":"'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'"}' > "$STATS_FILE"
fi

ACTION="${1:-show}"

case "$ACTION" in
  --reset)
    echo '{"calls":0,"bytes_saved":0,"calls_blocked":0,"since":"'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'"}' > "$STATS_FILE"
    echo "✅ Stats reset"
    exit 0
    ;;
  --json)
    cat "$STATS_FILE"
    exit 0
    ;;
  --record)
    # Internal: called by post_tool_call hook to record a ctx_execute call
    BYTES="$2"
    python3 -c "
import json, sys
with open('$STATS_FILE') as f: d = json.load(f)
d['calls'] += 1
d['bytes_saved'] += int(sys.argv[1])
with open('$STATS_FILE', 'w') as f: json.dump(d, f)
" "$BYTES"
    exit 0
    ;;
  --blocked)
    # Internal: called by ctx-redirect hook to count blocked terminal calls
    python3 -c "
import json
with open('$STATS_FILE') as f: d = json.load(f)
d['calls_blocked'] += 1
with open('$STATS_FILE', 'w') as f: json.dump(d, f)
"
    exit 0
    ;;
  show|*)
    python3 -c "
import json
with open('$STATS_FILE') as f: d = json.load(f)
mb = d['bytes_saved'] / (1024*1024)
print(f'📊 ctx-stats (since {d[\"since\"][:10]})')
print(f'   ctx_execute calls: {d[\"calls\"]}')
print(f'   terminal blocked:  {d[\"calls_blocked\"]}')
print(f'   bytes saved:       {d[\"bytes_saved\"]:,} ({mb:.1f} MB)')
if d['calls'] > 0:
    print(f'   avg save/call:     {d[\"bytes_saved\"]//d[\"calls\"]:,} bytes (~75% reduction)')
"
    exit 0
    ;;
esac
