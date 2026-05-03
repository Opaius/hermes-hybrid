"""
hermes-hybrid v2.3 — Hermes Agent plugin for MCP tool optimization.

Bundles context-mode + mcp-visibility + RTK for Hermes Agent.
One npm install: npm i -g hermes-hybrid && hermes-hybrid setup.

Architecture (hooks-only, no handler monkey-patching):
  register(ctx):
    on_session_start      → register clean tool aliases from tool_aliases.yaml
    pre_tool_call         → security (blocks dangerous ctx_execute shell commands)
    pre_llm_call          → schema compaction (compact MCP tool descriptions in-place)
    transform_tool_result → smart formatting + caching for ALL tool results (incl. MCP)

Tool Aliasing:
  Users configure tool_aliases.yaml: { clean_name: ugly_mcp_backend_name }
  On session start, proxy tools are registered with clean names.
  LLM sees "cp_terminal" instead of "mcp_context_mode_ctx_execute".
  Dashboard/chats naturally display clean names.
  Token savings from shorter tool names in every API call.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

from .mcp_visibility import (
    _discover_all_tools,
    _check_command_security,
    _compact_description,
    _toon_convert,
    _cache_get,
    _cache_set,
    _RTK_ENABLED,
    _RTK_PATH,
    SHELL_EXEC_TOOLS,
    TOOL_ALIASES,
    TOOL_EMOJIS,
    _safe_name,
    pre_tool_call_security,
)

try:
    from .output_fmt import optimize as _optimize_result
except ImportError:
    _optimize_result = _toon_convert

# ── Tool aliasing config ──

_ALIASES_CONFIG_PATH = Path(__file__).parent / "tool_aliases.yaml"
_ALIASES_REGISTERED = False

# Map clean_name → ugly_mcp_name (built from config)
_ALIAS_MAP: dict[str, str] = {}
# Reverse: ugly_mcp_name → clean_name (for transform_tool_result display)
_ALIAS_REVERSE: dict[str, str] = {}


def _load_alias_config() -> dict[str, str]:
    """Load tool_aliases.yaml. Returns {clean_name: ugly_mcp_name}."""
    try:
        import yaml
    except ImportError:
        logger.debug("mcp-visibility: yaml not available, skipping aliases")
        return {}

    if not _ALIASES_CONFIG_PATH.exists():
        logger.debug("mcp-visibility: %s not found, skipping aliases", _ALIASES_CONFIG_PATH)
        return {}

    try:
        data = yaml.safe_load(_ALIASES_CONFIG_PATH.read_text())
        aliases = data.get("aliases", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception as e:
        logger.warning("mcp-visibility: failed to load %s: %s", _ALIASES_CONFIG_PATH, e)
        return {}


def _register_tool_aliases(**kwargs) -> None:
    """
    on_session_start hook: register clean-named proxy tools for all configured aliases.

    Fires once per session. By this time MCP tools are loaded in registry.
    Idempotent — only registers once per process lifetime.
    """
    global _ALIASES_REGISTERED, _ALIAS_MAP, _ALIAS_REVERSE
    if _ALIASES_REGISTERED:
        return

    aliases = _load_alias_config()
    if not aliases:
        _ALIASES_REGISTERED = True
        return

    try:
        from tools.registry import registry
    except ImportError:
        logger.debug("mcp-visibility: registry not available, skipping alias registration")
        _ALIASES_REGISTERED = True
        return

    registered = 0
    for clean_name, ugly_name in aliases.items():
        # Check source tool exists
        src_entry = registry.get_entry(ugly_name)
        if src_entry is None:
            logger.debug("mcp-visibility: alias '%s' → '%s' skipped (source not found)", clean_name, ugly_name)
            continue

        # Build a proxy handler that dispatches to the real MCP tool
        _target = ugly_name  # capture for closure

        def _make_proxy(target_name: str):
            def proxy_handler(args, **kw):
                return registry.dispatch(target_name, args, **kw)
            return proxy_handler

        try:
            registry.register(
                name=clean_name,
                toolset=src_entry.toolset,
                schema=src_entry.schema,
                handler=_make_proxy(_target),
                check_fn=src_entry.check_fn,
                requires_env=src_entry.requires_env,
                is_async=src_entry.is_async,
                description=src_entry.description,
                emoji=src_entry.emoji,
            )
            _ALIAS_MAP[clean_name] = ugly_name
            _ALIAS_REVERSE[ugly_name] = clean_name
            registered += 1
        except Exception as e:
            logger.warning("mcp-visibility: failed to register alias '%s': %s", clean_name, e)

    _ALIASES_REGISTERED = True
    if registered:
        logger.info("mcp-visibility: registered %d tool aliases from %s", registered, _ALIASES_CONFIG_PATH.name)


# ── Schema compaction: compact native MCP tool descriptions ──

_COMPACT_DONE = False

_NATIVE_COMPACT_MAP = {
    "mcp_context_mode_ctx_execute": (
        "Execute code in sandboxed subprocess. Only stdout enters context. "
        "Languages: shell, python, javascript, typescript, go, rust. "
        "PREFER over bash for API calls, test runners, data processing, git queries."
    ),
    "mcp_context_mode_ctx_batch_execute": (
        "Execute multiple commands in parallel with indexed output."
    ),
    "mcp_context_mode_ctx_search": (
        "Full-text search across indexed subprocess sessions and cached content."
    ),
    "mcp_context_mode_ctx_index": (
        "Index docs or knowledge content into searchable database."
    ),
    "mcp_context_mode_ctx_fetch_and_index": (
        "Fetch URL, convert to markdown, and index for search."
    ),
    "mcp_context_mode_ctx_stats": (
        "Get context consumption statistics for current session."
    ),
    "mcp_context_mode_ctx_doctor": (
        "Diagnose context-mode installation and dependencies."
    ),
    "mcp_context_mode_ctx_upgrade": (
        "Upgrade context-mode to latest version."
    ),
    "mcp_context_mode_ctx_purge": (
        "Permanently delete session data and indexed content."
    ),
    "mcp_context_mode_ctx_insight": (
        "Open context-mode analytics dashboard."
    ),
    "mcp_context_mode_ctx_execute_file": (
        "Read and process a file in sandboxed subprocess."
    ),
    "mcp_searxng_searxng_web_search": (
        "Web search via SearXNG. Returns title, URL, description for each result."
    ),
    "mcp_searxng_web_url_read": (
        "Fetch and extract content from a URL."
    ),
}


def _compact_schemas_pre_llm(**kwargs) -> None:
    """pre_llm_call hook (one-shot): compact native MCP tool descriptions in-place."""
    global _COMPACT_DONE
    if _COMPACT_DONE:
        return

    try:
        from tools.registry import registry
    except ImportError:
        logger.debug("mcp-visibility: tools.registry not available, skipping compaction")
        _COMPACT_DONE = True
        return

    try:
        compacted = 0
        for native_name, compact_desc in _NATIVE_COMPACT_MAP.items():
            entry = registry.get_entry(native_name)
            if entry is None:
                continue
            entry.description = _compact_description(compact_desc)
            # Also update alias tool description if it exists
            clean_name = _ALIAS_REVERSE.get(native_name)
            if clean_name:
                alias_entry = registry.get_entry(clean_name)
                if alias_entry:
                    alias_entry.description = _compact_description(compact_desc)
            compacted += 1

        if compacted:
            logger.info("mcp-visibility: compacted %d native MCP tool descriptions", compacted)
    except Exception as e:
        logger.warning("mcp-visibility: schema compaction failed: %s", e)
    finally:
        _COMPACT_DONE = True


# ── transform_tool_result: smart formatting + caching ──

def _transform_tool_result(
    tool_name: str = "",
    args: dict = None,
    result: str = "",
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int = 0,
    **kwargs,
) -> str | None:
    """
    transform_tool_result hook: format + cache MCP tool results.

    Also handles alias tools — resolves clean_name back to mcp_ name for caching.
    """
    # Resolve alias → original for lookup
    actual_tool = _ALIAS_MAP.get(tool_name, tool_name)

    # Only format MCP tools (or their aliases)
    if not actual_tool.startswith("mcp_"):
        return None

    if not result or not isinstance(result, str):
        return None

    stripped = result.strip()
    if not stripped:
        return None

    try:
        # Cache under original MCP name (aliases share cache)
        cache_name = actual_tool
        cached = _cache_get(cache_name, args or {})
        if cached:
            logger.debug("mcp-visibility: cache hit for %s (via %s)", cache_name, tool_name)
            return cached

        optimized = _optimize_result(stripped, actual_tool)

        if optimized != stripped:
            orig_bytes = len(stripped.encode("utf-8"))
            opt_bytes = len(optimized.encode("utf-8"))
            saved = round((1 - opt_bytes / max(orig_bytes, 1)) * 100)
            display_name = _ALIAS_REVERSE.get(actual_tool, actual_tool)
            logger.info(
                "mcp-visibility: transform_tool_result formatted %s (%d→%d bytes, %d%% saved)",
                display_name, orig_bytes, opt_bytes, saved,
            )

        _cache_set(cache_name, args or {}, optimized)
        return optimized
    except Exception as e:
        logger.debug("mcp-visibility: transform_tool_result failed for %s: %s", tool_name, e)
        return None


# ── Plugin entry point ──

def register(ctx) -> None:
    """Register all hooks. No handler swap — native Hermes hooks only."""

    # on_session_start: register tool aliases (MCP tools available by now)
    try:
        ctx.register_hook("on_session_start", _register_tool_aliases)
        logger.info("mcp-visibility: registered on_session_start alias registration hook")
    except Exception as e:
        logger.warning("mcp-visibility: on_session_start hook failed: %s", e)

    # pre_tool_call: security for ctx_execute shell commands
    try:
        ctx.register_hook("pre_tool_call", pre_tool_call_security)
        logger.info("mcp-visibility: registered pre_tool_call security hook")
    except Exception as e:
        logger.warning("mcp-visibility: pre_tool_call hook failed: %s", e)

    # pre_llm_call: compact MCP tool descriptions (one-shot, in-place)
    try:
        ctx.register_hook("pre_llm_call", _compact_schemas_pre_llm)
        logger.info("mcp-visibility: registered pre_llm_call schema compaction hook")
    except Exception as e:
        logger.warning("mcp-visibility: pre_llm_call hook failed: %s", e)

    # transform_tool_result: smart formatting + caching for ALL tools
    try:
        ctx.register_hook("transform_tool_result", _transform_tool_result)
        logger.info("mcp-visibility: registered transform_tool_result formatting hook")
    except Exception as e:
        logger.warning("mcp-visibility: transform_tool_result hook failed: %s", e)

    logger.info("mcp-visibility: ready — hooks registered")
