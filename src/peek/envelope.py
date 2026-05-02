"""Untrusted-content envelope.

Every chunk of window text we hand back to the agent is wrapped:

    <window_text source="<app>:<title>" trust="untrusted">
    {escaped text}
    </window_text>

The wrapper is a syntactic signal that the content is *not* an authoritative
instruction. It mitigates prompt injection from log lines / browser tabs /
chat messages that say "ignore previous instructions and...". The README's
threat model section documents this; the wrapper itself is here.

Escaping rules:
- Zero-width characters (U+200B/200C/200D/FEFF) are stripped from input so
  an attacker can't pre-inject `<‍window_text trust="trusted">` and
  evade the literal replacement that would otherwise neutralize the tag.
- Pre-existing `<window_text>` and `</window_text>` tags are matched
  case-insensitively (LLMs treat XML tags case-insensitively) and
  zero-width-broken so they can't close our wrapper or open a fake one.
- Source string has `"`, `<`, `>`, and control characters scrubbed so an
  attacker cannot break out of the source attribute.
"""

from __future__ import annotations

import re

# We deliberately use a zero-width joiner instead of HTML entity escapes:
# the wrapper isn't HTML, and entity escapes would mangle log content
# that legitimately contains '&' or '<'. ZWJ keeps log lines visually
# identical for the agent while breaking literal tag matching.
_ZWJ = "‍"

# Zero-width characters that LLMs ignore but that would let an attacker
# pre-collide with our neutralization marker if left in the input.
_ZERO_WIDTH_CHARS = ("​", "‌", "‍", "﻿")

OPEN_LITERAL = "<window_text"
CLOSE_LITERAL = "</window_text>"

OPEN_NEUTRALIZED = f"<{_ZWJ}window_text"
CLOSE_NEUTRALIZED = f"</{_ZWJ}window_text>"

# Case-insensitive matchers. We anchor on the literal "window_text" so a
# variant like "<WINDOW_TEXT" still gets neutralized — LLMs do not treat
# XML tag case as semantically meaningful.
_OPEN_RE = re.compile(r"<\s*window_text", re.IGNORECASE)
_CLOSE_RE = re.compile(r"<\s*/\s*window_text\s*>", re.IGNORECASE)


def _escape_source_attr(source: str) -> str:
    """Escape characters that would break the `source="..."` attribute.

    We replace " with ' and strip control characters. We also collapse any
    embedded `>` so an attacker cannot close our open tag from inside
    the attribute. This is intentionally aggressive — the source field
    is only ever displayed to an LLM, never rendered as HTML.
    """
    out_chars: list[str] = []
    for ch in source:
        if ch == '"':
            out_chars.append("'")
        elif ch in ("<", ">"):
            out_chars.append(" ")
        elif ch in _ZERO_WIDTH_CHARS:
            # zero-widths in source could collide with our neutralization
            out_chars.append(" ")
        elif ord(ch) < 0x20 and ch not in ("\t",):
            # control chars (incl. NUL, newline) are dropped
            out_chars.append(" ")
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _strip_zero_width(text: str) -> str:
    """Remove zero-width characters that LLMs ignore but that bypass tag escaping."""
    if not text:
        return text
    for zw in _ZERO_WIDTH_CHARS:
        if zw in text:
            text = text.replace(zw, "")
    return text


def _neutralize_inner_tags(text: str) -> str:
    """Break literal `<window_text>` / `</window_text>` tags in input.

    Strips zero-widths first so attackers can't pre-collide with the
    neutralization marker. Matches case-insensitively because LLMs treat
    `<WINDOW_TEXT>` the same as `<window_text>`.
    """
    if not text:
        return text
    text = _strip_zero_width(text)
    # Order matters: replace closing first so we don't double-process the
    # closing's substring of the opening literal.
    text = _CLOSE_RE.sub(CLOSE_NEUTRALIZED, text)
    text = _OPEN_RE.sub(OPEN_NEUTRALIZED, text)
    return text


def wrap(text: str, source: str) -> str:
    """Return the envelope-wrapped version of `text`.

    `source` is something like `"Console.app:system.log"` — included so the
    agent has a tag to refer to the content by. We escape it defensively.
    """
    safe_source = _escape_source_attr(source)
    safe_text = _neutralize_inner_tags(text or "")
    return (
        f'<window_text source="{safe_source}" trust="untrusted">\n'
        f"{safe_text}\n"
        "</window_text>"
    )
