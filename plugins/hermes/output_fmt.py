#!/usr/bin/env python3
"""
output_fmt.py — Smart output-type-aware formatting for LLM tool results.

Replaces TOON with LLM-trained formats (markdown tables, YAML, truncation).
TOON available as fallback via MCP_VISIBILITY_FMT=toon env var.

Detection → format → metadata header → return.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Tuple

logger = logging.getLogger(__name__)

FMT_MODE = os.getenv("MCP_VISIBILITY_FMT", "smart")  # "smart" | "toon" | "passthrough"
TRUNCATE_LINES = int(os.getenv("MCP_VISIBILITY_TRUNCATE", "100"))
SMALL_THRESHOLD = 200  # chars — below this, keep JSON as-is


def _to_markdown_table(rows: list[dict], keys: list[str]) -> str:
    """Convert list of uniform dicts to pipe-separated markdown table."""
    if not rows:
        return "_(empty)_"

    # Header row
    header = "| " + " | ".join(keys) + " |"
    # Separator row
    sep = "| " + " | ".join("---" for _ in keys) + " |"

    lines = [header, sep]
    for row in rows:
        vals = []
        for k in keys:
            v = str(row.get(k, ""))
            # Escape pipes inside values
            v = v.replace("|", "\\|").replace("\n", " ")
            vals.append(v)
        lines.append("| " + " | ".join(vals) + " |")

    return "\n".join(lines)


def _to_yaml(data: dict) -> str:
    """Convert dict to YAML using stdlib-safe approach."""
    import yaml
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True).strip()


def _strip_html(html: str) -> str:
    """Basic HTML tag stripping — removes tags, preserves text."""
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _make_header(fmt: str, meta: dict) -> str:
    """Generate one-line format hint for the LLM."""
    parts = [f"fmt={fmt}"]
    if "rows" in meta:
        parts.append(f"rows={meta['rows']}")
        if "cols" in meta:
            parts.append(f"cols={meta['cols']}")
    if "saved" in meta or "saved_pct" in meta:
        val = int(meta.get("saved", meta.get("saved_pct", 0)))
        parts.append(f"saved={val}%")
    return f"[vis {' '.join(parts)}]"


def format_result(raw: str, tool_name: str = "") -> Tuple[str, dict]:
    """
    Format tool result for LLM consumption.

    Args:
        raw: Raw result string from tool execution.
        tool_name: Tool name for context-aware formatting.

    Returns:
        (formatted_str, metadata_dict)
        metadata: {fmt, original_bytes, formatted_bytes, savings_pct, [rows, cols]}
    """
    original_bytes = len(raw.encode("utf-8"))
    meta: dict[str, Any] = {
        "fmt": "passthrough",
        "original_bytes": original_bytes,
        "formatted_bytes": original_bytes,
        "savings_pct": 0.0,
    }

    stripped = raw.strip()
    if not stripped:
        return raw, meta

    # ── Passthrough mode ──
    if FMT_MODE == "passthrough":
        return raw, meta

    # ── TOON mode ──
    if FMT_MODE == "toon":
        from .mcp_visibility import _toon_convert
        result = _toon_convert(raw)
        if result != raw:
            formatted_bytes = len(result.encode("utf-8"))
            savings = round((1 - formatted_bytes / max(original_bytes, 1)) * 100)
            header = _make_header("toon", {"saved_pct": savings})
            meta["fmt"] = "toon"
            meta["formatted_bytes"] = formatted_bytes
            meta["savings_pct"] = savings
            return f"{header}\n{result}", meta
        return raw, meta

    # ── Smart mode ──
    # Try JSON parse
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if parsed is not None:
        # Empty
        if isinstance(parsed, (list, dict)) and len(parsed) == 0:
            meta["fmt"] = "passthrough"
            return raw, meta

        # JSON array of uniform dicts → markdown table
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            keys = list(parsed[0].keys())
            if len(keys) <= 8:
                table = _to_markdown_table(parsed, keys)
                formatted_bytes = len(table.encode("utf-8"))
                savings = round((1 - formatted_bytes / max(original_bytes, 1)) * 100)
                header = _make_header("md-table", {
                    "rows": len(parsed),
                    "cols": len(keys),
                    "saved_pct": savings,
                })
                meta["fmt"] = "md-table"
                meta["rows"] = len(parsed)
                meta["cols"] = len(keys)
                meta["formatted_bytes"] = formatted_bytes
                meta["savings_pct"] = savings
                return f"{header}\n{table}", meta
            # Too many columns — fall through to YAML-ish or JSON

        # JSON dict → YAML
        if isinstance(parsed, dict):
            if original_bytes < SMALL_THRESHOLD:
                return raw, meta  # Keep small JSON as-is
            try:
                yaml_str = _to_yaml(parsed)
                formatted_bytes = len(yaml_str.encode("utf-8"))
                savings = round((1 - formatted_bytes / max(original_bytes, 1)) * 100)
                header = _make_header("yaml", {"saved_pct": savings})
                meta["fmt"] = "yaml"
                meta["formatted_bytes"] = formatted_bytes
                meta["savings_pct"] = savings
                return f"{header}\n{yaml_str}", meta
            except Exception:
                return raw, meta  # YAML dump failed — passthrough

        # JSON that doesn't match patterns → passthrough
        return raw, meta

    # ── Non-JSON content ──
    # HTML detection
    if stripped.startswith("<") and ("</" in stripped or "/>" in stripped):
        try:
            text = _strip_html(stripped)
            if text:
                formatted_bytes = len(text.encode("utf-8"))
                savings = round((1 - formatted_bytes / max(original_bytes, 1)) * 100)
                header = _make_header("markdown", {"saved_pct": savings})
                meta["fmt"] = "markdown"
                meta["formatted_bytes"] = formatted_bytes
                meta["savings_pct"] = savings
                return f"{header}\n{text}", meta
        except Exception:
            pass
        return raw, meta

    # Plain text truncation
    lines = stripped.split("\n")
    if len(lines) > TRUNCATE_LINES:
        truncated = "\n".join(lines[:TRUNCATE_LINES])
        leftover = len(lines) - TRUNCATE_LINES
        footer = f"\n[... omitted={leftover} more lines. Use ctx_search to query specific sections.]"
        result = truncated + footer
        formatted_bytes = len(result.encode("utf-8"))
        savings = round((1 - formatted_bytes / max(original_bytes, 1)) * 100)
        header = _make_header("truncated", {
            "rows": TRUNCATE_LINES,
            "saved_pct": savings,
        })
        meta["fmt"] = "truncated"
        meta["formatted_bytes"] = formatted_bytes
        meta["savings_pct"] = savings
        meta["total_lines"] = len(lines)
        meta["omitted"] = leftover
        return f"{header}\n{result}", meta

    # Everything else → passthrough
    return raw, meta


# ── Public API wrappers ──

def format_header(fmt: str, **kwargs) -> str:
    """Public wrapper for the format hint header."""
    return _make_header(fmt, kwargs)


def optimize(raw: str, tool_name: str = "") -> str:
    """Public one-shot: format and return the result string (with header)."""
    formatted, _ = format_result(raw, tool_name)
    return formatted
