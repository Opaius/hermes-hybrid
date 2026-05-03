"""
hermes-hybrid v2.2 — Hermes Agent plugin for MCP tool optimization.

Bundles context-mode + mcp-visibility + RTK for Hermes Agent.
One npm install: npm i -g hermes-hybrid && hermes-hybrid setup.

Architecture (hooks-only, no handler monkey-patching):
  register(ctx):
    pre_tool_call        → security (blocks dangerous ctx_execute shell commands)
    pre_llm_call         → schema compaction (compact MCP tool descriptions in-place)
    transform_tool_result → smart formatting + caching for ALL tool results (incl. MCP)

Why transform_tool_result:
  - Fires for EVERY tool call in handle_function_call (model_tools.py:762)
  - Works for MCP tools where post_tool_call does NOT fire
  - Receives original result → returns formatted string to replace it
  - Built-in Hermes Agent v0.11.0+ hook (tests/test_transform_tool_result_hook.py)
"""
from __future__ import annotations

import json
import logging

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

# Import formatting engine
try:
    from .output_fmt import optimize as _optimize_result
except ImportError:
    _optimize_result = _toon_convert  # fallback to TOON


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
    """
    pre_llm_call hook (one-shot): compact native MCP tool descriptions in-place.

    Modifies registry entries directly — no handler swap needed.
    Returns None (observational hook — side effects only).
    """
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
            compacted += 1

        if compacted:
            logger.info(
                "mcp-visibility: compacted %d native MCP tool descriptions",
                compacted,
            )
    except Exception as e:
        logger.warning("mcp-visibility: schema compaction failed: %s", e)
    finally:
        _COMPACT_DONE = True


# ── transform_tool_result: smart formatting + caching for all tools ──

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

    Fires for EVERY tool call (handle_function_call, model_tools.py:762).
    Works where post_tool_call doesn't (MCP tools bypass post_tool_call).

    Args:
        tool_name: Full tool name (e.g. "mcp_context_mode_ctx_execute")
        args: Tool arguments dict
        result: Raw result string (JSON or plain text)
        duration_ms: Tool execution time in ms

    Returns:
        Formatted string to replace the result, or None to keep original.
        First non-None string return from any hook wins (model_tools.py:771-774).
    """
    # Only format MCP tool results
    if not tool_name.startswith("mcp_"):
        return None

    if not result or not isinstance(result, str):
        return None

    stripped = result.strip()
    if not stripped:
        return None

    try:
        # Check cache
        cached = _cache_get(tool_name, args or {})
        if cached:
            logger.debug("mcp-visibility: cache hit for %s", tool_name)
            return cached

        # Apply smart formatting
        optimized = _optimize_result(stripped, tool_name)

        if optimized != stripped:
            orig_bytes = len(stripped.encode("utf-8"))
            opt_bytes = len(optimized.encode("utf-8"))
            saved = round((1 - opt_bytes / max(orig_bytes, 1)) * 100)
            logger.info(
                "mcp-visibility: transform_tool_result formatted %s (%d→%d bytes, %d%% saved)",
                tool_name, orig_bytes, opt_bytes, saved,
            )

        # Cache the result
        _cache_set(tool_name, args or {}, optimized)

        return optimized
    except Exception as e:
        logger.debug("mcp-visibility: transform_tool_result failed for %s: %s", tool_name, e)
        return None


# ── Plugin entry point ──

def register(ctx) -> None:
    """Register hooks. No handler swap — native Hermes hooks only."""

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
