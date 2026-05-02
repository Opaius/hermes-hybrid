"""
mcp-visibility — Hermes Agent plugin for MCP tool optimization.

Hooks-only architecture: modifies native MCP tools in-place.
Works WITH or WITHOUT context-mode MCP server installed.

Architecture:
  register(ctx) → registers hooks only (no wrapper tools)
  pre_tool_call hook → blocks dangerous shell commands
  pre_llm_call hook (one-shot) → compacts native MCP tool descriptions
  post_tool_call hook → TOON conversion + result caching
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
    SHELL_EXEC_TOOLS,
    TOOL_ALIASES,
    TOOL_EMOJIS,
    _safe_name,
    pre_tool_call_security,
)

# Import new modules
try:
    from .output_fmt import optimize as _optimize_result
except ImportError:
    _optimize_result = _toon_convert  # fallback to TOON


def _post_tool_call_optimize(
    tool_name: str, args: dict, result: str, **kwargs
) -> str:
    """post_tool_call hook: optimize native MCP results (fire-and-forget log)."""
    if not tool_name.startswith("mcp_"):
        return result
    stripped = result.strip() if isinstance(result, str) else ""
    if not stripped:
        return result
    try:
        cache_hit = _cache_get(tool_name, args)
        if cache_hit:
            return cache_hit
        optimized = _optimize_result(stripped, tool_name)
        _cache_set(tool_name, args, optimized)
        return optimized
    except Exception:
        return result


# ── One-shot pre_llm_call hook: compact native MCP tool descriptions ──

_COMPACT_DONE = False

# Map native MCP tool names → compact descriptions
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
    # searxng
    "mcp_searxng_searxng_web_search": (
        "Web search via SearXNG. Returns title, URL, description for each result."
    ),
    "mcp_searxng_web_url_read": (
        "Fetch and extract content from a URL."
    ),
}


def _compact_native_tool_schemas(**kwargs) -> None:
    """
    pre_llm_call hook (one-shot): compact native MCP tool descriptions in-place.

    Gracefully skips tools that aren't registered (e.g. context-mode not installed).
    Also wraps shell execution tool handlers with security checks.
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
                continue  # Tool not registered — skip silently
            orig_len = len(entry.description or "")
            entry.description = _compact_description(compact_desc)
            new_len = len(entry.description or "")
            compacted += 1
            logger.debug(
                "mcp-visibility: compacted %s (%d→%d chars)",
                native_name, orig_len, new_len,
            )

        if compacted:
            logger.info(
                "mcp-visibility: compacted %d native MCP tool descriptions",
                compacted,
            )

        # Wrap shell execution tool handlers with security checks
        swapped = 0
        for native_name in ("mcp_context_mode_ctx_execute",
                           "mcp_context_mode_ctx_batch_execute"):
            entry = registry.get_entry(native_name)
            if entry is None:
                continue  # Tool not registered — skip silently
            original_handler = entry.handler

            def _make_secure_handler(orig, tool_name):
                _fmt = _optimize_result
                _get = _cache_get
                _set = _cache_set

                def secure_handler(args, **kw):
                    inner = args.get("arguments", args)
                    language = inner.get("language", "")
                    code = inner.get("code", "")

                    # Pre-execution: security check via unified security module
                    if language == "shell" and code:
                        try:
                            from .security import check_all_command_guards
                            result = check_all_command_guards(code, "local")
                            if not result.get("approved"):
                                if result.get("status") == "approval_required":
                                    return json.dumps({
                                        "output": "", "exit_code": -1,
                                        "error": result.get("message", "Waiting for user approval"),
                                        "status": "approval_required",
                                        "command": code,
                                        "description": result.get("description", "command flagged"),
                                        "pattern_key": result.get("pattern_key", ""),
                                    }, ensure_ascii=False)
                                return json.dumps({
                                    "output": "", "exit_code": -1,
                                    "error": result.get("message", "Command blocked"),
                                    "status": "blocked",
                                }, ensure_ascii=False)
                        except ImportError:
                            pass

                    # Cache check
                    cached = _get(tool_name, args) if _get else None
                    if cached:
                        return cached

                    # Execute via original handler
                    result = orig(args, **kw)

                    # Post-execution: smart formatting + cache
                    if _fmt:
                        result_str = str(result)
                        try:
                            parsed = json.loads(result_str)
                            if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], str):
                                inner = parsed["result"]
                                formatted_inner = _fmt(inner, tool_name)
                                parsed["result"] = formatted_inner
                                result = json.dumps(parsed, ensure_ascii=False)
                            else:
                                result = _fmt(result_str, tool_name)
                        except Exception:
                            result = _fmt(result_str, tool_name)
                    if _set:
                        _set(tool_name, args, str(result))

                    return result
                return secure_handler

            entry.handler = _make_secure_handler(original_handler, native_name)
            swapped += 1

        if swapped:
            logger.info(
                "mcp-visibility: swapped %d shell tool handlers (approval-aware)",
                swapped,
            )
    except Exception as e:
        logger.warning("mcp-visibility: schema compaction failed: %s", e)
    finally:
        _COMPACT_DONE = True


def register(ctx) -> None:
    """Register hooks. No wrapper tools — works with or without MCP servers."""
    tools = _discover_all_tools()
    tool_count = len(tools)

    # Only register pre_tool_call hook as fallback when handler swap won't cover it
    try:
        from tools.registry import registry
        has_ctx_execute = registry.get_entry("mcp_context_mode_ctx_execute") is not None
        has_ctx_batch = registry.get_entry("mcp_context_mode_ctx_batch_execute") is not None
    except ImportError:
        has_ctx_execute = False
        has_ctx_batch = False

    if not (has_ctx_execute or has_ctx_batch):
        try:
            ctx.register_hook("pre_tool_call", pre_tool_call_security)
            logger.info("mcp-visibility: registered pre_tool_call security hook (fallback)")
        except Exception as e:
            logger.debug("mcp-visibility: pre_tool_call hook skipped: %s", e)
    else:
        logger.info("mcp-visibility: handler swap covers security, skipping redundant pre_tool_call hook")

    # One-shot hook: compact native MCP descriptions before first LLM call
    try:
        ctx.register_hook("pre_llm_call", _compact_native_tool_schemas)
        logger.info(
            "mcp-visibility: registered pre_llm_call schema compaction hook (%d tools)",
            tool_count,
        )
    except Exception as e:
        logger.debug("mcp-visibility: pre_llm_call hook skipped: %s", e)

    logger.info("mcp-visibility: ready — %d tools discovered, guardrails active", tool_count)
