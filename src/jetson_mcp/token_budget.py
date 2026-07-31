"""
token_budget.py

Small helper for keeping MCP tool return values within a sane token budget.

Any tool that shells out and returns raw stdout/stderr (pip list, dpkg,
colcon logs, ssh output, grep results, etc.) can blow past what's
reasonable to hand back to an LLM in one tool_result. This module gives
you a single choke point to clip that text before it goes back to Claude.

Usage:
    from token_budget import clip_output

    return clip_output(result.stdout, max_tokens=2000, label="pip list")

Or as a decorator on a tool function that returns a string:
    @token_limited(max_tokens=2000)
    def my_tool(...) -> str:
        ...
        return big_text
"""

from __future__ import annotations

import functools
from typing import Callable

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

except ImportError:
    # Fallback heuristic if tiktoken isn't installed: ~4 chars/token for
    # English/code text. Not exact, but good enough to catch runaway output.
    _ENC = None

    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


def clip_output(
    text: str,
    max_tokens: int = 2000,
    label: str = "output",
    keep: str = "tail",
) -> str:
    """
    Clip `text` to roughly `max_tokens` tokens, keeping either the tail
    (default -- most recent/relevant for logs) or the head.

    Returns the original text unchanged if it's already within budget.
    """
    if not text:
        return text

    total = count_tokens(text)
    if total <= max_tokens:
        return text

    # Binary-search-free approximate clip: estimate a char cutoff from the
    # ratio of tokens we need to drop, then nudge if we overshot.
    ratio = max_tokens / total
    cutoff = max(1, int(len(text) * ratio))

    if keep == "head":
        clipped = text[:cutoff]
        # Trim to a clean line boundary so we don't cut mid-line.
        clipped = clipped.rsplit("\n", 1)[0] if "\n" in clipped else clipped
        notice = (
            f"\n\n[...clipped: showing first ~{max_tokens} of ~{total} "
            f"estimated tokens from {label}...]"
        )
        return clipped + notice
    else:
        clipped = text[-cutoff:]
        clipped = clipped.split("\n", 1)[-1] if "\n" in clipped else clipped
        notice = (
            f"[...clipped: showing last ~{max_tokens} of ~{total} "
            f"estimated tokens from {label}...]\n\n"
        )
        return notice + clipped


def token_limited(max_tokens: int = 2000, label: str | None = None, keep: str = "tail"):
    """
    Decorator for MCP tool functions that return a plain string.
    Wraps the return value through clip_output() automatically.

    @mcp.tool()
    @token_limited(max_tokens=1500)
    def inspect_installed_packages(...) -> str:
        ...
    """

    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        tool_label = label or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> str:
            result = func(*args, **kwargs)
            if not isinstance(result, str):
                return result
            return clip_output(result, max_tokens=max_tokens, label=tool_label, keep=keep)

        return wrapper

    return decorator