"""
hermes-hybrid v2.6.0 — Hermes Agent plugin for MCP tool optimization.

Bundles context-mode + mcp-visibility + RTK for Hermes Agent.
One npm install: npm i -g hermes-hybrid && hermes-hybrid setup.

Architecture (hooks-only, no handler monkey-patching):
  register(ctx):
    pre_tool_call         → security (blocks dangerous ctx_execute shell commands)
    pre_llm_call          → schema compaction (compact MCP tool descriptions in-place)
    transform_tool_result → file_cache → rtk_compress → output_fmt → result_cache

transform_tool_result pipeline:
  1. File read caching — if file unchanged since last read, return ~30-token stub (lean-ctx style)
  2. RTK compression — strip ANSI, collapse blanks, remove noise from shell output
  3. Output formatting — md-table/YAML/truncation/compressed-JSON (output_fmt.py)
  4. Result caching — hash-keyed cache with TTL per tool type
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from .mcp_visibility import (
    _compact_description,
    _toon_convert,
    _cache_get,
    _cache_set,
    _rtk_compress_result,
    _file_cache_check,
    pre_tool_call_security,
)

try:
    from .output_fmt import optimize as _optimize_result
except ImportError:
    _optimize_result = _toon_convert

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
            compact_desc = _compact_description(compact_desc)

            entry = registry.get_entry(native_name)
            if entry:
                entry.description = compact_desc
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

    Pipeline: file_cache → rtk_compress → output_fmt → result_cache
    """
    # Only format MCP tools
    if not tool_name.startswith("mcp_"):
        return None

    if not result or not isinstance(result, str):
        return None

    stripped = result.strip()
    if not stripped:
        return None

    try:
        # ── File read caching (lean-ctx style) ──
        file_stub = _file_cache_check(tool_name, args or {}, stripped)
        if file_stub:
            logger.info("mcp-visibility: file cache hit %s", tool_name)
            return file_stub

        # ── RTK-style post-execution compression ──
        rtk_compressed, rtk_changed = _rtk_compress_result(tool_name, args or {}, stripped)
        if rtk_changed:
            orig_len = len(stripped)
            stripped = rtk_compressed
            logger.info(
                "mcp-visibility: rtk compressed %s (%d→%d bytes, %d%% saved)",
                tool_name, orig_len, len(stripped),
                round((1 - len(stripped) / max(orig_len, 1)) * 100),
            )

        # ── Result cache check ──
        cache_name = tool_name
        cached = _cache_get(cache_name, args or {})
        if cached:
            logger.debug("mcp-visibility: cache hit for %s", cache_name)
            return cached

        # ── Output formatting (md-table/YAML/truncation) ──
        optimized = _optimize_result(stripped, tool_name)

        if optimized != stripped:
            orig_bytes = len(stripped.encode("utf-8"))
            opt_bytes = len(optimized.encode("utf-8"))
            saved = round((1 - opt_bytes / max(orig_bytes, 1)) * 100)
            logger.info(
                "mcp-visibility: transform_tool_result formatted %s (%d→%d bytes, %d%% saved)",
                tool_name, orig_bytes, opt_bytes, saved,
            )

        _cache_set(cache_name, args or {}, optimized)
        return optimized
    except Exception as e:
        logger.debug("mcp-visibility: transform_tool_result failed for %s: %s", tool_name, e)
        return None


# ── Plugin entry point ──

def register(ctx) -> None:
    """Register all hooks. No handler swap — native Hermes hooks only."""

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
