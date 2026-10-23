"""MCP tools for deterministic, local regex-based text sanitization."""

from __future__ import annotations

import re

from fastmcp import FastMCP


mcp = FastMCP("MCP Security Sanitizer")

_EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+(?![\w.-])"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?"
    r"(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)"
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|
        refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*
        (?:'[^'\r\n]+'|"[^"\r\n]+"|[^\s,;]+)""",
        re.VERBOSE,
    ),
)


def _secret_matches(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in _SECRET_PATTERNS)


def _mask_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


@mcp.tool()
def mask_api_keys_and_secrets(text: str) -> str:
    """Redact OpenAI-style API keys, bearer tokens, and common named secrets."""
    return _mask_secrets(text)


@mcp.tool()
def mask_pii_data(text: str) -> str:
    """Redact email addresses and common North American phone numbers."""
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return _PHONE_RE.sub("[REDACTED_PHONE]", text)


@mcp.tool()
def audit_text_for_risks(text: str) -> str:
    """Count recognizable secrets, email addresses, and phone numbers."""
    counts = {
        "secrets": _secret_matches(text),
        "emails": len(_EMAIL_RE.findall(text)),
        "phone_numbers": len(_PHONE_RE.findall(text)),
    }
    return (
        "Compliance risk counts: "
        f"secrets={counts['secrets']}, "
        f"emails={counts['emails']}, "
        f"phone_numbers={counts['phone_numbers']}"
    )


def main() -> None:
    """Run the MCP server over the requested SSE transport."""
    mcp.run(transport="sse", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
