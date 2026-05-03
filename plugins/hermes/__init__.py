"""
hermes-hybrid — Hermes Agent plugin for MCP tool optimization.

Bundles context-mode + mcp-visibility + RTK for Hermes Agent.
One npm install: npm i -g hermes-hybrid && hermes-hybrid setup.

Hooks-only architecture: modifies native MCP tools in-place.
Works WITH or WITHOUT context-mode MCP server installed.

Architecture:
  register(ctx) → registers 3 hooks
  pre_tool_call hook → terminal whitelist + ctx_execute security
  pre_llm_call hook (one-shot) → compacts native MCP tool descriptions + handler swap (ALL tools)
  post_tool_call hook → formatting cache fallback (formatted if handler swap missed)
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

# Import new modules
try:
    from .output_fmt import optimize as _optimize_result
except ImportError:
    _optimize_result = _toon_convert  # fallback to TOON


# Tools that get security checks in handler swap (shell exec)
_SHELL_SECURITY_TOOLS = {"mcp_context_mode_ctx_execute", "mcp_context_mode_ctx_batch_execute"}

# All tools whose handlers are swapped (set dynamically after swap)
_SWAPPED_TOOLS = set()


def _post_tool_call_optimize(
    tool_name: str, args: dict, result: str, **kwargs
) -> str:
    """post_tool_call hook: formatting + cache fallback for MCP tools not covered by handler swap.
    
    NOTE: Hermes gateway may not invoke post_tool_call for MCP tools.
    All formatting is handled by the handler swap in _compact_native_tool_schemas.
    This hook is the safety net.
    """
    if not tool_name.startswith("mcp_"):
        return result
    logger.info("mcp-visibility: post_tool_call fired for %s", tool_name)
    if tool_name in _SWAPPED_TOOLS:
        return result  # Already formatted + cached by handler swap
    stripped = result.strip() if isinstance(result, str) else ""
    if not stripped:
        return result
    try:
        cache_hit = _cache_get(tool_name, args)
        if cache_hit:
            logger.debug("mcp-visibility: post_tool_call cache hit for %s", tool_name)
            return cache_hit
        optimized = _optimize_result(stripped, tool_name)
        if optimized != stripped:
            orig_bytes = len(stripped.encode("utf-8"))
            opt_bytes = len(optimized.encode("utf-8"))
            saved = round((1 - opt_bytes / max(orig_bytes, 1)) * 100)
            logger.info(
                "mcp-visibility: post_tool_call formatted %s (%d→%d bytes, %d%% saved)",
                tool_name, orig_bytes, opt_bytes, saved,
            )
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
    pre_llm_call hook (one-shot): compact native MCP tool descriptions in-place,
    then wrap ALL MCP tool handlers with formatting/caching (and security for shell tools).

    Gracefully skips tools that aren't registered (e.g. context-mode not installed).
    """
    global _COMPACT_DONE
    logger.info("mcp-visibility: compaction hook called (COMPACT_DONE=%s)", _COMPACT_DONE)
    if _COMPACT_DONE:
        return

    try:
        from tools.registry import registry
    except ImportError:
        logger.debug("mcp-visibility: tools.registry not available, skipping compaction")
        _COMPACT_DONE = True
        return

    try:
        # ── Phase 1: Compact descriptions ──
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

        # ── Phase 2: Handler swap — ALL tools ──
        # Shared format+cache post-execution logic
        def _format_and_cache(result, tool_name, args):
            """Apply smart formatting + cache. Returns formatted result."""
            fmt = _optimize_result
            _set = _cache_set
            if not fmt:
                return result
            try:
                result_str = str(result)
                try:
                    parsed = json.loads(result_str)
                    if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], str):
                        inner = parsed["result"]
                        formatted_inner = fmt(inner, tool_name)
                        if formatted_inner != inner:
                            orig_bytes = len(inner.encode("utf-8"))
                            opt_bytes = len(formatted_inner.encode("utf-8"))
                            saved = round((1 - opt_bytes / max(orig_bytes, 1)) * 100)
                            logger.info(
                                "mcp-visibility: handler-swap formatted %s (%d→%d bytes, %d%% saved)",
                                tool_name, orig_bytes, opt_bytes, saved,
                            )
                        parsed["result"] = formatted_inner
                        result = json.dumps(parsed, ensure_ascii=False)
                    else:
                        formatted = fmt(result_str, tool_name)
                        if formatted != result_str:
                            logger.info("mcp-visibility: handler-swap formatted %s", tool_name)
                        result = formatted
                except Exception:
                    result = fmt(result_str, tool_name)
            except Exception:
                pass
            if _set:
                try:
                    _set(tool_name, args, str(result))
                except Exception:
                    pass
            return result

        def _make_secure_handler(orig, tool_name):
            """Security check + RTK wrap + format + cache for shell exec tools."""
            _get = _cache_get

            def secure_handler(args, **kw):
                inner = args.get("arguments", args)
                language = inner.get("language", "")
                code = inner.get("code", "")

                # Pre-execution: security check
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

                # RTK wrapping: prepend rtk to shell commands for token savings
                if language == "shell" and code and _RTK_ENABLED:
                    try:
                        wrapped = f"{_RTK_PATH} -- {code}"
                        if "code" in inner:
                            inner["code"] = wrapped
                        if "code" in args:
                            args["code"] = wrapped
                        logger.debug("mcp-visibility: rtk wrapping active — %s → %s", code[:60], wrapped[:60])
                    except Exception:
                        pass

                # Cache check
                cached = _get(tool_name, args) if _get else None
                if cached:
                    return cached

                # Execute via original handler
                result = orig(args, **kw)

                # Post-execution: smart formatting + cache
                return _format_and_cache(result, tool_name, args)

            return secure_handler

        def _make_format_handler(orig, tool_name):
            """Format-only handler — no security. For read-only/search/fetch MCP tools."""
            _get = _cache_get

            def format_handler(args, **kw):
                cached = _get(tool_name, args) if _get else None
                if cached:
                    return cached
                result = orig(args, **kw)
                return _format_and_cache(result, tool_name, args)

            return format_handler

        swapped = 0
        secured = 0
        formatted = 0
        for native_name in _NATIVE_COMPACT_MAP:
            entry = registry.get_entry(native_name)
            if entry is None:
                continue
            original_handler = entry.handler

            if native_name in _SHELL_SECURITY_TOOLS:
                entry.handler = _make_secure_handler(original_handler, native_name)
                secured += 1
            else:
                entry.handler = _make_format_handler(original_handler, native_name)
                formatted += 1
            swapped += 1

        # Update _SWAPPED_TOOLS so post_tool_call knows which to skip
        global _SWAPPED_TOOLS
        _SWAPPED_TOOLS = {
            name for name in _NATIVE_COMPACT_MAP
            if registry.get_entry(name) is not None
        }

        if swapped:
            logger.info(
                "mcp-visibility: swapped %d MCP tool handlers (%d with security, %d format-only)",
                swapped, secured, formatted,
            )
    except Exception as e:
        logger.warning("mcp-visibility: schema compaction failed: %s", e)
    finally:
        _COMPACT_DONE = True


def register(ctx) -> None:
    """Register hooks. Always registers pre_tool_call for terminal whitelist + ctx_execute security."""
    tools = _discover_all_tools()
    tool_count = len(tools)

    # Always register pre_tool_call — handles terminal whitelist AND ctx_execute security
    try:
        ctx.register_hook("pre_tool_call", pre_tool_call_security)
        logger.info("mcp-visibility: registered pre_tool_call security hook (terminal+ctx)")
    except Exception as e:
        logger.warning("mcp-visibility: pre_tool_call hook failed: %s", e)

    # One-shot hook: compact native MCP descriptions + handler swap for ALL tools
    # Uses on_session_start to guarantee it fires on every new conversation,
    # not just the first LLM turn (which may have been consumed by a stale plugin).
    try:
        ctx.register_hook("on_session_start", _compact_native_tool_schemas)
        ctx.register_hook("pre_llm_call", _compact_native_tool_schemas)
        logger.info(
            "mcp-visibility: registered pre_llm_call + on_session_start schema compaction hooks (%d tools)",
            tool_count,
        )
    except Exception as e:
        logger.debug("mcp-visibility: schema compaction hooks skipped: %s", e)

    # Post-tool-call hook: safety net for MCP tools not covered by handler swap
    try:
        ctx.register_hook("post_tool_call", _post_tool_call_optimize)
        logger.info("mcp-visibility: registered post_tool_call optimization hook")
    except Exception as e:
        logger.debug("mcp-visibility: post_tool_call hook skipped: %s", e)

    logger.info("mcp-visibility: ready — %d tools discovered, guardrails active", tool_count)
