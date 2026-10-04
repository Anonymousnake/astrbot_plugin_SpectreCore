"""Bound visible speech without cutting quote or meme markup in half."""

import re

MARKUP = re.compile(r"&&[^&\s]+&&|[\[【]\s*引用\s*[:：][^\]】\n]+[\]】]")
TOKENS = re.compile(MARKUP.pattern + r"|[\s\S]")


def limit_reply(text: str, max_chars: int) -> str:
    """Prefer a complete sentence or clause, with a strict visible-text ceiling."""
    if max_chars <= 0 or text.strip() == "<NO_RESPONSE>":
        return text
    visible = MARKUP.sub("", text)
    if len(visible) <= max_chars:
        return text
    head = visible[:max_chars]
    cut, suffix = max_chars - 1, "…"
    for pattern, ending in ((r"[。！？!?；;\n]", ""), (r"[，,、：:]", "。")):
        boundaries = list(re.finditer(pattern, head))
        if boundaries and boundaries[-1].end() >= max_chars / 2:
            cut = boundaries[-1].end() - bool(ending)
            suffix = ending
            break
    parts, count = [], 0
    for token in TOKENS.findall(text):
        if MARKUP.fullmatch(token):
            parts.append(token)
        else:
            if count >= cut:
                break
            parts.append(token)
            count += 1
    return "".join(parts).rstrip() + suffix
