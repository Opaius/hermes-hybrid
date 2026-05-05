# hermes-hybrid v2.6.0

**One setup. Full token efficiency stack. No npm publication needed.**

AI agents should follow this README to self-install. Every component is composable
through `--flags` and JSON output. The agent reads `action` fields in responses
to decide next steps.

## Quick Start (AI Agent — Self-Install)

```
# 1. Clone the repo
git clone https://github.com/Opaius/hermes-hybrid.git /opt/hermes-workspace/hermes-hybrid

# 2. Install system dependencies (one-time)
bun --version || curl -fsSL https://bun.sh/install | bash
pip install scrapling[all] && scrapling install

# 3. Start SearXNG (private search — no Tor, Webshare proxy)
cd /opt/hermes-workspace/hermes-hybrid
mkdir -p searxng && chmod 777 searxng
export SEARXNG_SECRET=$(openssl rand -hex 32)
docker compose -f docker-compose.searxng.yml up -d
# SearXNG at http://localhost:3211

# 4. Configure Hermes Agent
# Add to ~/.hermes/config.yaml:
#   plugins:
#     enabled:
#       - hermes-hybrid
#   mcp_servers:
#     searxng:
#       command: bunx
#       args: ["mcp-searxng"]
#       env:
#         SEARXNG_URL: "http://localhost:3211"

# 5. Install the plugin
cp -r /opt/hermes-workspace/hermes-hybrid/plugins/hermes ~/.hermes/plugins/hermes-hybrid/

# 6. Start context-mode MCP server (Hermes config must reference this)
#    Add to ~/.hermes/config.yaml mcp_servers:
#      context-mode:
#        command: bunx
#        args: ["context-mode"]

# 7. Restart WebUI
systemctl restart hermes-webui
# or: hermes-hybrid restart
```

## What Gets Installed

| Component | Path | Purpose |
|-----------|------|---------|
| Hermes plugin | `~/.hermes/plugins/hermes-hybrid/` | Formatting, security, cache, RTK compression, file cache, skill injection |
| scrapling-fetch.py | `scripts/scrapling-fetch.py` | Web fetcher — Chrome impersonation, proxy, CSS extraction, StealthyFetcher |
| SearXNG Docker | `docker-compose.searxng.yml` | Private metasearch — no Tor, Webshare proxy, Redis cache |
| context-mode | via `bunx context-mode` | Sandboxed execution, BM25-indexed output |
| Scrapling Python | via `pip install scrapling[all]` | HTTP + headless browser fetching |

## Composable Pipeline

```
                      ┌─────────────────────┐
                      │   Need web data?     │
                      └─────────┬───────────┘
                                │
                  ┌─────────────┼──────────────┐
                  ▼             ▼              ▼
              SEARCH        FETCH URL      SCREENSHOT
         SearXNG MCP    ctx_fetch_and_*  vision_analyze
                                │
                  ┌─────────────┼──────────────┐
                  ▼             ▼              ▼
              SIMPLE         FAILED?        CUSTOM
         ctx_fetch_and_   scrapling-fetch   ctx_execute
         index (BM25)         .py         python code
                                │
                          ctx_index + ctx_search
```

## scrapling-fetch.py — All Flags

Every flag is composable. The script outputs structured JSON with an `action` field
telling the agent what to do next.

| Flag | Default | What it does |
|------|---------|-------------|
| `--url URL` | *(required)* | Target URL |
| `--stealth` | off | StealthyFetcher: Playwright browser, JS render, Cloudflare bypass |
| `--select "css"` | "" | CSS selector extraction (e.g. `article p,h2`) |
| `--raw` | off | Raw HTML output (no markdown conversion) |
| `--text-only` | off | Strip ALL tags, plain text only |
| `--extract-links` | off | Extract all `<a href>` links |
| `--max-links N` | 100 | Max links to extract |
| `--compact` | off | No content in JSON → agent MUST call `ctx_index` |
| `--index-source "label"` | "" | Sets action: `call_ctx_index` or `optional_ctx_index` |
| `--output-file /tmp/x.md` | "" | Write content to file |
| `--timeout N` | 30 | Request timeout seconds |
| `--impersonate` | chrome_131 | Browser impersonation: chrome_131, firefox_133, safari_18 |
| `--proxy "url"` | Webshare | Custom proxy. `none` disables proxy. |
| `--no-proxy` | off | Disable proxy entirely |
| `--headers "K:V"` | [] | Extra HTTP headers (repeatable) |
| `--no-headless` | off | Show browser window in stealth mode (debug) |
| `--no-network-idle` | off | Don't wait for network idle in stealth mode |
| `--help` | — | Full usage with examples |

### Output `action` field

| Value | Meaning |
|-------|---------|
| `call_ctx_index` | Compact mode. Agent MUST call `ctx_index(content=<content>, source=<index_source>)` |
| `optional_ctx_index` | Full mode. Content in JSON. Agent can optionally `ctx_index` for BM25 search |
| `content_in_context` | No `--index-source` set. Content in JSON, no index hint |
| `use_cli_fallback` | Python API failed. Agent should use `scrapling extract get ...` CLI |
| `try_ctx_fetch_and_index` | All failed. Agent should use `ctx_fetch_and_index` as last resort |

## Plugin Architecture (v2.6)

The Hermes plugin uses **three native Hermes Agent hooks** — zero monkey-patching:

| Hook | When | Purpose |
|------|------|---------|
| `pre_tool_call` | Before any tool | Security: block dangerous ctx_execute shell commands |
| `pre_llm_call` | Before every LLM turn | Schema compaction + auto-inject web-fetch skill on first turn |
| `transform_tool_result` | After every tool | 4-stage pipeline: file_cache → rtk_compress → output_fmt → result_cache |

**`transform_tool_result` pipeline:**

1. **File read caching** — Unchanged file re-reads return ~30-token stub (lean-ctx style)
2. **RTK compression** — Strip ANSI, collapse blanks, remove noise from shell output
3. **Output formatting** — md-table/csv/truncation/compressed-JSON (output_fmt.py)
4. **Result caching** — Hash-keyed cache with TTL per tool type

## Env Var Kill Switches

| Var | Effect | Default |
|-----|--------|---------|
| `HH_RTK_COMPRESS=0` | Disable RTK shell output compression | 1 |
| `HH_FILE_CACHE=0` | Disable file read caching | 1 |
| `HH_FILE_CACHE_MAX=N` | Max cached file entries (LRU) | 500 |
| `MCP_VISIBILITY_SECURITY=0` | Disable shell command blocking | 1 |
| `MCP_VISIBILITY_FMT=passthrough` | No output formatting | smart |
| `MCP_VISIBILITY_CACHE=0` | No result caching | 1 |
| `MCP_VISIBILITY_SCHEMA_COMPACT=0` | No MCP schema compaction | 1 |

Set in `~/.hermes/.env` or export before running.

## Requirements

- **Hermes Agent** — plugin host
- **bun** ≥ 1.0 — for context-mode MCP server
- **Python 3.11+** — for plugin + scrapling-fetch.py
- **Scrapling** — `pip install scrapling[all] && scrapling install`
- **Docker** — for SearXNG
- **Webshare account** — for rotating proxy ($2.99/mo, 250GB)

## Docker Compose (SearXNG)

```bash
cd hermes-hybrid
mkdir -p searxng && chmod 777 searxng
export SEARXNG_SECRET=$(openssl rand -hex 32)
docker compose -f docker-compose.searxng.yml up -d
```

Services: SearXNG (port 3211) + Redis (cache). No Tor, no Whoogle. Proxy via Webshare.

## Verification

```bash
# 1. SearXNG health
curl http://localhost:3211/healthz

# 2. Scrapling fetch
python3 scripts/scrapling-fetch.py --url https://httpbin.org/html --select h1
# Must return JSON with status: ok, title: "Herman Melville - Moby-Dick"

# 3. Full pipeline
python3 scripts/scrapling-fetch.py --url https://httpbin.org/html --compact --index-source test 2>/dev/null
# Must return action: call_ctx_index
# Then: ctx_index(content=<markdown>, source="test") → ctx_search(queries=["Moby"], source="test")

# 4. Plugin loaded
grep "mcp-visibility: ready" ~/.hermes/logs/agent.log | tail -1
# Must show: mcp-visibility: ready — hooks registered
```

## License

MIT © [cioky](https://github.com/cioky)
