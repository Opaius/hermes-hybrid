<p align="center">
  <img src="https://img.shields.io/npm/v/hermes-hybrid?color=blue" alt="npm">
  <img src="https://img.shields.io/badge/platform-Hermes%20%7C%20OpenCode-purple" alt="platforms">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
</p>

# hermes-hybrid

**One install. Three battle-tested tools. Zero token waste.**

`hermes-hybrid` bundles the best toolchain for Hermes Agent and OpenCode into a single npm package — context-mode sandboxed execution, smart output formatting, security guardrails, and RTK command rewriting.

<p align="center">
  <b>npm i -g hermes-hybrid && hermes-hybrid setup</b>
</p>

---

## What's Inside

| Component | What It Does | Token Savings | Author |
|-----------|-------------|---------------|--------|
| **[context-mode](https://github.com/mksglu/context-mode)** | Sandboxed multi-language execution (JS/TS/Python/Go/Rust). Raw data stays out of context. BM25-indexed output search. | ~98% context reduction | [@mksglu](https://github.com/mksglu) |
| **[rtk](https://github.com/rtk-ai/rtk)** | Shell command rewriting proxy. `git diff` → `rtk diff`. 60-90% smaller output. | 60–90% | [rtk-ai](https://github.com/rtk-ai) |
| **[mcp-visibility](https://github.com/Opaius/hermes-mcp-visibility)** | Smart formatting (md-table/YAML/truncation), security guardrails, result caching. Dual plugin for Hermes + OpenCode. | 20–60% per call | [@cioky](https://github.com/Opaius) |

## Quick Start

```bash
# Install
npm i -g hermes-hybrid

# Auto-configure
hermes-hybrid setup

# Verify
hermes-hybrid doctor

# Start context-mode server
hermes-hybrid serve
```

Restart Hermes after setup:
```bash
hermes gateway restart
```

## CLI Reference

```bash
hermes-hybrid setup      # Auto-detect Hermes + OpenCode, install plugins, configure MCP
hermes-hybrid serve      # Start context-mode MCP server
hermes-hybrid doctor     # Health check all integrations
hermes-hybrid status     # Show env vars + versions
hermes-hybrid remove     # Clean uninstall everything
hermes-hybrid env        # Print env var reference

# Alias
hh setup
```

## Configuration

**Granular kill switches. Disable only what you don't need.**

```bash
# In ~/.hermes/.env or export before running

HH_SERVER=0            # Disable context-mode MCP server
HH_VISIBILITY=0        # Disable all mcp-visibility features
HH_FMT=passthrough     # No formatting (smart | toon | passthrough)
HH_SECURITY=0          # No command security checks
HH_CACHE=0             # No result caching
HH_COMPACT=0           # No schema compaction
HH_SANDBOX=bun         # Runtime: bun | deno | node
HH_TRUNCATE=100        # Lines before truncation
```

**Example:** want context-mode but no visibility?

```bash
export HH_VISIBILITY=0
hermes-hybrid serve
```

**Example:** want visibility formatting but no security blocking?

```bash
export HH_SECURITY=0
hermes gateway restart
```

## What Gets Installed

| Target | Path | Purpose |
|--------|------|---------|
| Hermes plugin | `~/.hermes/plugins/mcp-visibility/` | Formatting, security, cache for ctx_execute |
| OpenCode plugin | `~/.config/opencode/plugins/mcp-visibility.ts` | Formatting + cache for OpenCode |
| Hermes config | `~/.hermes/config.yaml` | MCP server entries, hooks |
| context-mode | via `bunx context-mode` | Sandboxed execution server |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Hermes Agent                     │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ ctx_exec │  │ security │  │ output_fmt    │  │
│  │ (sandbox)│──│ (guard)  │──│ (md-table/    │  │
│  │          │  │          │  │  YAML/trunc)  │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│       │                            │              │
│       ▼                            ▼              │
│  context-mode                 mcp-visibility      │
│  MCP server                   plugin              │
│       │                            │              │
│       ▼                            ▼              │
│  ┌──────────────────────────────────────┐        │
│  │            LLM Context                │        │
│  │    (formatted, truncated, indexed)    │        │
│  └──────────────────────────────────────┘        │
│                                                   │
│  Terminal commands: rtk rewrite → 60-90% savings  │
└─────────────────────────────────────────────────┘
```

## Manual Install (no npm)

```bash
# Clone and run setup
git clone https://github.com/Opaius/hermes-hybrid.git
cd hermes-hybrid
node bin/cli.js setup
```

## Requirements

- **Hermes Agent** (for Hermes plugin)
- **OpenCode** (for OpenCode plugin)
- **bun** or **node** ≥18 (for context-mode server)
- **rtk** CLI (optional, for command rewriting)
- **Python 3.11+** (for Hermes plugin — format/security modules)

## License

MIT © [cioky](https://github.com/cioky)

---

### Acknowledgments

This project stands on the shoulders of:

- **[context-mode](https://github.com/mksglu/context-mode)** by [@mksglu](https://github.com/mksglu) — The MCP server that makes sandboxed execution possible. Without it, every `ls`, `git diff`, and `curl` would flood your context window.
- **[rtk](https://github.com/rtk-ai/rtk)** by [rtk-ai](https://github.com/rtk-ai) — The CLI proxy that rewrites shell commands for 60-90% token reduction. The silent workhorse behind every shell call.
- **[hermes-mcp-visibility](https://github.com/Opaius/hermes-mcp-visibility)** — Smart formatting, security, and caching layer that makes raw tool output LLM-readable.
