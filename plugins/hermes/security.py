#!/usr/bin/env python3
"""
security.py — Unified security module for mcp-visibility.

Replaces both mcp_visibility._check_command_security and ctx-redirect.sh patterns.
"""
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ENABLED = os.getenv("MCP_VISIBILITY_SECURITY", "1") == "1"

# ── Hardline patterns: unconditionally blocked ──
HARDLINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-rf\s+/(\s|$)", re.MULTILINE), "recursive root delete"),
    (re.compile(r"\bmkfs\b"), "mkfs"),
    (re.compile(r"\bdd\s+if=.*of=/dev/(sd|nvme|mmcblk)"), "dd to raw device"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk)"), "redirect to raw device"),
    (re.compile(r"\bchmod\s+777\s+(/etc|/usr|/bin|/sbin|/lib|/boot|/sys|/proc|/dev)"), "chmod 777 on system dir"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "system power command"),
    (re.compile(r"\bkill\s+-9\s+1\b"), "kill init"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"), "fork bomb"),
    (re.compile(r"\b(wget|curl)\s.*\|\s*(sh|bash)\b"), "pipe to shell"),
]

# ── Approval-required patterns: triggers Hermes approval UI ──
APPROVAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bchmod\s+777\b"), "chmod 777"),
    (re.compile(r"\bfind\s+.*-delete\b"), "find -delete"),
    (re.compile(r"\brm\s+-rf\b"), "rm -rf (non-root)"),
    (re.compile(r"\bgit\s+push\s+--force\s+(origin\s+)?(main|master)\b"), "force push to main"),
    (re.compile(r"\bdocker\s+(rm|rmi)\s+.*(-f|--force)"), "docker force remove"),
    (re.compile(r"\bsystemctl\s+(stop|disable)\s+.*hermes"), "stop hermes service"),
]

# ── Passthrough prefixes: always allowed ──
PASSTHROUGH_PREFIXES = ["rtk ", "hermes ", "bg:", "pty:", "which ", "type "]

# ── Evasion detection ──
EVASION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\becho\s+[A-Za-z0-9+/=]{20,}\s*\|\s*base64\s+-d\s*\|\s*(sh|bash)\b"), "base64 pipe evasion"),
    (re.compile(r"\$\{?IFS\}?.*rm"), "IFS evasion"),
    (re.compile(r"\bbash\s+<\("), "process substitution"),
    (re.compile(r"\\x[0-9a-fA-F]{2}.*rm"), "hex escape evasion"),
]


def check_command(code: str, language: str = "shell", session_id: str = "") -> dict:
    """
    Single authoritative security check.

    Returns:
        {"approved": bool, "status": "approved"|"blocked"|"approval_required",
         "message": str, "description": str, "pattern_key": str}
    """
    if not ENABLED or language != "shell":
        return {"approved": True, "status": "approved", "message": "", "description": "", "pattern_key": ""}

    # Check passthrough prefixes
    for prefix in PASSTHROUGH_PREFIXES:
        if code.startswith(prefix):
            return {"approved": True, "status": "approved", "message": "", "description": "", "pattern_key": ""}

    # Check evasion patterns first (always blocked)
    for pattern, label in EVASION_PATTERNS:
        if pattern.search(code):
            msg = f"Security blocked: evasion attempt ({label})"
            audit_log({"tool": "ctx_execute", "lang": language, "blocked": True, "reason": label, "session": session_id})
            return {"approved": False, "status": "blocked", "message": msg, "description": label, "pattern_key": label}

    # Check hardline patterns
    for pattern, label in HARDLINE_PATTERNS:
        if pattern.search(code):
            msg = f"\u26d4 BLOCKED: {label}"
            audit_log({"tool": "ctx_execute", "lang": language, "blocked": True, "reason": label, "session": session_id})
            return {"approved": False, "status": "blocked", "message": msg, "description": label, "pattern_key": label}

    # Check approval patterns
    for pattern, label in APPROVAL_PATTERNS:
        if pattern.search(code):
            msg = f"Command requires approval: {label}"
            audit_log({"tool": "ctx_execute", "lang": language, "status": "approval_required", "reason": label, "session": session_id})
            return {"approved": False, "status": "approval_required", "message": msg, "description": label, "pattern_key": label}

    return {"approved": True, "status": "approved", "message": "", "description": "", "pattern_key": ""}


def audit_log(event: dict) -> None:
    """Append to ~/.hermes/vis-audit.jsonl"""
    try:
        log_path = Path(os.path.expanduser("~/.hermes/vis-audit.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("audit_log failed: %s", e)


def check_all_command_guards(code: str, backend: str = "local") -> dict:
    """
    Wrapper that mimics Hermes' tools.approval.check_all_command_guards interface.
    For backward compatibility with handler swap code.
    """
    result = check_command(code, "shell")
    return {
        "approved": result["approved"],
        "status": result["status"],
        "message": result["message"],
        "description": result.get("description", ""),
        "pattern_key": result.get("pattern_key", ""),
    }
