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
- Pre-existing `<window_text>` and `</window_text>` tags in the input are
  zero-width-broken so they can't close our wrapper or open a fake one.
- Source string has its `"` and special chars escaped so an attacker can't
  break out of the source attribute.
"""

from __future__ import annotations

# We deliberately use a zero-width joiner instead of HTML entity escapes:
# the wrapper isn't HTML, and entity escapes would mangle log content
# that legitimately contains '&' or '<'. ZWJ keeps log lines visually
# identical for the agent while breaking literal tag matching.
_ZWJ = "‍"

OPEN_LITERAL = "<window_text"
CLOSE_LITERAL = "</window_text>"

OPEN_NEUTRALIZED = f"<{_ZWJ}window_text"
CLOSE_NEUTRALIZED = f"</{_ZWJ}window_text>"


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
        elif ord(ch) < 0x20 and ch not in ("\t",):
            # control chars (incl. NUL, newline) are dropped
            out_chars.append(" ")
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _neutralize_inner_tags(text: str) -> str:
    """Break literal `<window_text>` / `</window_text>` tags in input."""
    if not text:
        return text
    # Order matters: replace closing first so we don't double-process the
    # closing's substring of the opening literal.
    text = text.replace(CLOSE_LITERAL, CLOSE_NEUTRALIZED)
    text = text.replace(OPEN_LITERAL, OPEN_NEUTRALIZED)
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
