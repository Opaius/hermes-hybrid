#!/usr/bin/env node
/**
 * hermes-hybrid postinstall
 * 
 * Prints setup guidance. Doesn't auto-install — user runs `hermes-hybrid setup`.
 */

const GREEN = "\x1b[32m";
const CYAN = "\x1b[36m";
const BOLD = "\x1b[1m";
const NC = "\x1b[0m";

console.log(`
${CYAN}╔══════════════════════════════════════════╗
║   hermes-hybrid installed!                ║
╚══════════════════════════════════════════╝${NC}

  ${BOLD}Next:${NC} run ${GREEN}hermes-hybrid setup${NC} to auto-configure.

  ${BOLD}What this installs:${NC}
  • context-mode MCP server (sandboxed execution)
  • mcp-visibility plugins (formatting + security + cache)
  • RTK integration (command rewriting, 60-90% token savings)

  ${BOLD}Granular control:${NC}
  HH_SERVER=0           → disable context-mode
  HH_VISIBILITY=0       → disable formatting/security/cache
  HH_FMT=passthrough    → no formatting
  HH_SECURITY=0         → no command blocking

  ${BOLD}Docs:${NC} https://github.com/cioky/hermes-hybrid
`);
