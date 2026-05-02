#!/usr/bin/env bash
# ctx-redirect hook — blocks bare terminal() calls in Hermes Agent and
# forces ctx_execute for all shell commands.
#
# Part of hermes-ctx-enhance: https://github.com/cioky/hermes-ctx-enhance
#
# Hermes config (add to ~/.hermes/config.yaml):
#   hooks:
#     pre_tool_call:
#       - command: /path/to/hermes-ctx-enhance/hooks/ctx-redirect.sh
#         matcher: terminal
#
# Behaviour:
#   - Blocks bare terminal() calls → returns error telling LLM to use ctx_execute
#   - Allows background/pty calls (servers, interactive sessions)
#   - Allows rtk-prefixed commands (migration path)
#   - Allows non-terminal tools (passthrough)

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

# Passthrough non-terminal tools
if [ "$TOOL_NAME" != "terminal" ]; then
  exit 0
fi

CMD=$(echo "$INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin).get('tool_input',{}); print(a.get('command',''))" 2>/dev/null || echo "")
IS_BG=$(echo "$INPUT" | python3 -c "import json,sys; a=json.load(sys.stdin).get('tool_input',{}); print(1 if a.get('background') or a.get('pty') else 0)" 2>/dev/null || echo "0")

# Allow background/pty (servers, long-running processes)
if [ "$IS_BG" = "1" ]; then
  exit 0
fi

# Allow if already using rtk (migration path for LLM adaptation)
if echo "$CMD" | grep -qE '^rtk\b'; then
  exit 0
fi

# Allow context-mode's own shell commands (ctx_execute runs shell internally)
if echo "$CMD" | grep -qE '^hermes\b|^which\b|^type\b'; then
  exit 0
fi

# Block bare terminal — instruct ctx_execute
cat << 'BLOCK'
{"action": "block", "message": "⛔ TERMINAL BLOCKED by ctx-redirect. Use: mcp_context_mode_ctx_execute(language=\"shell\", code=\"<cmd>\") — saves ~75% tokens via sandboxed execution."}
BLOCK
