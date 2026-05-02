#!/usr/bin/env python3
"""
mcp-visibility — Hermes Agent plugin for MCP tool optimization.

Hooks-only architecture: modifies native MCP tools in-place via Hermes hooks.
No wrapper tools registered — works WITH or WITHOUT context-mode MCP server.

Features (all independently togglable via env vars):
  MCP_VISIBILITY_SECURITY=1     Security checks for shell commands
  MCP_VISIBILITY_TOON=1         JSON→TOON output conversion (40-60% token ↓)
  MCP_VISIBILITY_CACHE=1        File-based result caching
  MCP_VISIBILITY_SCHEMA_COMPACT=1  Truncate tool descriptions

Hooks registered:
  pre_tool_call  → blocks dangerous shell commands (security guardrail)
  pre_llm_call   → compacts native MCP tool descriptions (one-shot)
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Feature flags ──────────────────────────────────────────────────

_SECURITY_ENABLED = os.getenv("MCP_VISIBILITY_SECURITY", "1") == "1"
_TOON_ENABLED = os.getenv("MCP_VISIBILITY_TOON", "1") == "1"
_CACHE_ENABLED = os.getenv("MCP_VISIBILITY_CACHE", "1") == "1"
_SCHEMA_COMPACT = os.getenv("MCP_VISIBILITY_SCHEMA_COMPACT", "1") == "1"

_CACHE_DIR = Path(os.path.expanduser(
    os.getenv("MCP_VISIBILITY_CACHE_DIR", "~/.hermes/cache/mcp-visibility")
))

# ── Configuration ──────────────────────────────────────────────────

# Auto-detect hierarchy path: env var → default locations → None
def _detect_hierarchy() -> Optional[Path]:
    if env_path := os.getenv("MCP_VISIBILITY_HIERARCHY"):
        p = Path(os.path.expanduser(env_path))
        if p.is_dir():
            return p
    for candidate in [
        Path.home() / "hermes-agency/lazy-mcp/hierarchy-hermes",
        Path.home() / ".hermes/cache/mcp-visibility/hierarchy",
    ]:
        if candidate.is_dir():
            return candidate
    return None

LAZY_MCP_HIERARCHY = _detect_hierarchy()

# Short preferred names for well-known MCP tools
TOOL_ALIASES = {
    "context-mode.ctx_execute": "ctx_execute",
    "context-mode.ctx_search": "ctx_search",
    "context-mode.ctx_index": "ctx_index",
    "context-mode.ctx_fetch_and_index": "ctx_fetch",
    "context-mode.ctx_batch_execute": "ctx_batch",
    "context-mode.ctx_stats": "ctx_stats",
    "context-mode.ctx_doctor": "ctx_doctor",
    "context-mode.ctx_upgrade": "ctx_upgrade",
    "context-mode.ctx_purge": "ctx_purge",
    "context-mode.ctx_insight": "ctx_insight",
    "context-mode.ctx_execute_file": "ctx_exec_file",
    "searxng.search": "web_search",
    "searxng.searxng_web_search": "web_search",
    "searxng.web_url_read": "web_read",
}

TOOL_EMOJIS = {
    "ctx_execute": "⚡", "ctx_search": "🔎", "ctx_index": "📚",
    "ctx_fetch": "📥", "ctx_stats": "📊", "ctx_doctor": "🏥",
    "web_search": "🔍", "web_read": "📖",
}

# Tools whose shell commands need security checks
SHELL_EXEC_TOOLS = {"context-mode.ctx_execute", "context-mode.ctx_batch_execute"}

# Cache TTL by tool type (seconds)
CACHE_TTL = {
    "search": 300,
    "read": 600,
    "execute": 60,
    "default": 300,
}


# ── Schema compaction ──────────────────────────────────────────────

def _compact_description(desc: str, max_len: int = 140) -> str:
    """Truncate and clean up tool description for token savings."""
    if not desc or not _SCHEMA_COMPACT:
        return desc
    for prefix in [
        "Use this tool to ", "This tool allows you to ",
        "This endpoint ", "Use this endpoint to ",
    ]:
        if desc.startswith(prefix):
            desc = desc[len(prefix):]
            break
    if len(desc) > max_len:
        truncated = desc[:max_len - 3]
        for sep in [". ", "? ", "! "]:
            last = truncated.rfind(sep)
            if last > max_len // 2:
                return truncated[:last + 1]
        last_space = truncated.rfind(" ")
        if last_space > max_len // 2:
            return truncated[:last_space] + "…"
        return truncated + "…"
    return desc.strip()


# ── TOON conversion (inline, zero dependencies) ──────────────────

def _json_to_toon(data) -> str:
    """Convert Python dict/list to TOON-like compact format."""
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v):
                keys = list(v[0].keys())
                header = '{' + ','.join(keys) + '}'
                parts.append(f'{k}[{len(v)}]{header}')
                for item in v:
                    row = ','.join(str(item.get(kk, '')) for kk in keys)
                    parts.append(f'  {row}')
            elif isinstance(v, (int, float, bool, type(None))):
                parts.append(f'{k}={v}')
            elif isinstance(v, str):
                if any(c in v for c in ' ,=\n'):
                    parts.append(f'{k}={json.dumps(v)}')
                else:
                    parts.append(f'{k}={v}')
            else:
                parts.append(f'{k}={json.dumps(v)}')
        return '\n'.join(parts)
    elif isinstance(data, list):
        if not data:
            return '[]'
        if all(isinstance(x, dict) for x in data):
            keys = list(data[0].keys())
            header = f'items[{len(data)}]{{' + ','.join(keys) + '}'
            rows = [','.join(str(item.get(kk, '')) for kk in keys) for item in data]
            return header + '\n' + '\n'.join('  ' + r for r in rows)
        return json.dumps(data)
    return json.dumps(data)


def _toon_convert(data_str: str) -> str:
    """Convert JSON string to TOON format. Falls back silently to original."""
    if not _TOON_ENABLED:
        return data_str
    stripped = data_str.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return data_str
    try:
        parsed = json.loads(stripped)
        toon = _json_to_toon(parsed)
        if len(toon) < len(data_str):
            return toon
        return data_str
    except Exception:
        return data_str


# ── Result caching ─────────────────────────────────────────────────

def _cache_key(tool_path: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{tool_path}:{canonical}".encode()).hexdigest()[:16]


def _cache_get(tool_path: str, args: dict) -> Optional[str]:
    if not _CACHE_ENABLED:
        return None
    key = _cache_key(tool_path, args)
    cache_file = _CACHE_DIR / f"{key}.result"
    if not cache_file.exists():
        return None
    age = time.time() - cache_file.stat().st_mtime
    ttl = CACHE_TTL.get("default")
    for pattern, ttl_val in CACHE_TTL.items():
        if pattern in tool_path.lower():
            ttl = ttl_val
            break
    if age > ttl:
        cache_file.unlink(missing_ok=True)
        return None
    return cache_file.read_text()


def _cache_set(tool_path: str, args: dict, result: str) -> None:
    if not _CACHE_ENABLED:
        return
    key = _cache_key(tool_path, args)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / f"{key}.result").write_text(result)


# ── Tool discovery (for logging/status only) ──────────────────────

def _load_hierarchy_tools() -> Dict[str, Dict[str, Any]]:
    """Scan hierarchy dir, return {canonical_name: {server, tool, schema, desc}}."""
    tools = {}
    if not LAZY_MCP_HIERARCHY:
        return tools

    root = Path(LAZY_MCP_HIERARCHY)
    if not root.is_dir():
        return tools

    for srv_dir in sorted(root.iterdir()):
        if not srv_dir.is_dir():
            continue
        server = srv_dir.name
        for tf in sorted(srv_dir.glob("*.json")):
            try:
                data = json.loads(tf.read_text())
            except Exception:
                continue
            for tname, tdef in data.get("tools", {}).items():
                mcp_tool = tdef.get("maps_to", tname)
                canonical = f"{server}.{mcp_tool}"
                raw_desc = tdef.get("description", "")
                tools[canonical] = {
                    "server": server,
                    "tool": mcp_tool,
                    "schema": tdef.get("inputSchema", {}),
                    "desc": _compact_description(raw_desc),
                    "orig_desc": raw_desc,
                }

    logger.debug("mcp-visibility: found %d tools from hierarchy", len(tools))
    return tools


def _load_config_servers() -> List[Tuple[str, Dict[str, Any]]]:
    """Parse Hermes config.yaml, return [(server_name, server_config), ...]."""
    try:
        import yaml
    except ImportError:
        return []
    config_paths = [
        Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "config.yaml",
        Path.home() / ".hermes/config.yaml",
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                cfg = yaml.safe_load(config_path.read_text())
                servers = cfg.get("mcp_servers", {})
                if servers:
                    return list(servers.items())
            except Exception as e:
                logger.warning("mcp-visibility: failed to read %s: %s", config_path, e)
    return []


def _discover_all_tools() -> Dict[str, Dict[str, Any]]:
    """Discover MCP tools for logging/status. Not used for registration."""
    tools = {}
    hierarchy_tools = _load_hierarchy_tools()
    for name, info in hierarchy_tools.items():
        info["source"] = "hierarchy"
        tools[name] = info
    return tools


# ── Security ───────────────────────────────────────────────────────

def _check_command_security(shell_code: str) -> Optional[str]:
    if not _SECURITY_ENABLED:
        return None
    try:
        from tools.approval import check_all_command_guards
        result = check_all_command_guards(shell_code, "local")
        if not result.get("approved"):
            return json.dumps({
                "output": "",
                "exit_code": -1,
                "error": result.get("message", "Command blocked by security policy"),
                "status": result.get("status", "blocked"),
            }, ensure_ascii=False)
        return None
    except ImportError:
        return None
    except Exception as e:
        logger.warning("mcp-visibility: security check failed: %s", e)
        return json.dumps({
            "output": "", "exit_code": -1,
            "error": f"Security check error: {e}", "status": "blocked",
        }, ensure_ascii=False)


def _safe_name(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', s).strip('_') or "mcp"


# ── pre_tool_call hook ────────────────────────────────────────────

def pre_tool_call_security(tool_name: str, args: dict, **kwargs) -> Optional[dict]:
    """Block dangerous shell commands in ctx_execute / ctx_batch_execute."""
    if not _SECURITY_ENABLED:
        return None

    # Match native MCP ctx_execute tools
    if "ctx_execute" in tool_name.lower() or "ctx_batch" in tool_name.lower():
        language = args.get("language", "")
        code = args.get("code", "")
        if not code:
            inner = args.get("arguments", {})
            language = inner.get("language", language)
            code = inner.get("code", code)
        if language == "shell" and code:
            block_msg = _check_command_security(code)
            if block_msg:
                logger.info("mcp-visibility: BLOCKED native MCP call — %s", tool_name)
                return {"action": "block", "message": block_msg}
        return None

    return None
