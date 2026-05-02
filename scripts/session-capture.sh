#!/usr/bin/env bash
# session-capture hook — extracts structured events from PostToolUse calls.
# Tracks: file edits, git operations, errors, shell commands, web fetches.
#
# Part of hermes-ctx-enhance: https://github.com/cioky/hermes-ctx-enhance
#
# Hermes config (add to ~/.hermes/config.yaml):
#   hooks:
#     post_tool_call:
#       - command: /path/to/hermes-ctx-enhance/hooks/session-capture.sh
#
# Stores structured events in ~/.hermes/ctx-events/<session_id>.jsonl

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "unknown")
TOOL_INPUT=$(echo "$INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin).get('tool_input',{}); print(json.dumps(a))" 2>/dev/null || echo "{}")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ -z "$SESSION_ID" ] || [ "$SESSION_ID" = "unknown" ]; then
  exit 0
fi

EVENT_DIR="$HOME/.hermes/ctx-events"
mkdir -p "$EVENT_DIR"
EVENT_FILE="$EVENT_DIR/${SESSION_ID}.jsonl"

# Classify and extract
case "$TOOL_NAME" in
  terminal|bash|shell)
    CMD=$(echo "$TOOL_INPUT" | python3 -c "import json,sys; print(json.loads(sys.stdin).get('command',''))" 2>/dev/null || echo "")
    # Git operations
    if echo "$CMD" | grep -qE '^git\b'; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"git\",\"tool\":\"$TOOL_NAME\",\"cmd\":$(echo "$CMD" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:500]))")}" >> "$EVENT_FILE"
    # File mutations
    elif echo "$CMD" | grep -qE '\b(mv|cp|rm|mkdir|touch|chmod|chown)\b'; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"file_op\",\"tool\":\"$TOOL_NAME\",\"cmd\":$(echo "$CMD" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:500]))")}" >> "$EVENT_FILE"
    fi
    ;;
  read_file|view|read)
    PATH_VAL=$(echo "$TOOL_INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin); print(a.get('path','') or a.get('file_path','') or a.get('filePath',''))" 2>/dev/null || echo "")
    if [ -n "$PATH_VAL" ]; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"file_read\",\"tool\":\"$TOOL_NAME\",\"path\":$(echo "$PATH_VAL" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:500]))")}" >> "$EVENT_FILE"
    fi
    ;;
  write_file|write|patch)
    PATH_VAL=$(echo "$TOOL_INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin); print(a.get('path','') or a.get('file_path','') or a.get('filePath',''))" 2>/dev/null || echo "")
    if [ -n "$PATH_VAL" ]; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"file_write\",\"tool\":\"$TOOL_NAME\",\"path\":$(echo "$PATH_VAL" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:500]))")}" >> "$EVENT_FILE"
    fi
    ;;
  web_search|web_extract|browser_*|webclaw|curl|wget)
    URL=$(echo "$TOOL_INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin); print(a.get('url','') or a.get('query',''))" 2>/dev/null || echo "")
    if [ -n "$URL" ]; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"web\",\"tool\":\"$TOOL_NAME\",\"target\":$(echo "$URL" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:500]))")}" >> "$EVENT_FILE"
    fi
    ;;
  delegate_task|delegation)
    GOAL=$(echo "$TOOL_INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin); print(a.get('goal','') or a.get('prompt',''))" 2>/dev/null || echo "")
    if [ -n "$GOAL" ]; then
      echo "{\"ts\":\"$TIMESTAMP\",\"type\":\"delegation\",\"tool\":\"$TOOL_NAME\",\"goal\":$(echo "$GOAL" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read().strip()[:200]))")}" >> "$EVENT_FILE"
    fi
    ;;
esac

exit 0
