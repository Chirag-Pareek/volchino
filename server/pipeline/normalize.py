"""Stage 0: normalize the raw user utterance into a canonical intent string."""

from __future__ import annotations

import re
import unicodedata

_FILLER_PREFIXES = (
    r"hey volchino",
    r"ok volchino",
    r"volchino",
    r"wake up",
    r"please",
    r"can you",
    r"could you",
    r"would you",
    r"will you",
    r"i want you to",
    r"i'd like you to",
)
_PREFIX_RE = re.compile(r"^(?:(?:" + "|".join(_FILLER_PREFIXES) + r")[\s,]+)+")
_SUFFIX_RE = re.compile(r"[\s,]+(?:please|thanks|thank you|for me)$")


def normalize(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "")
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .!?;:")
    text = _PREFIX_RE.sub("", text)
    for _ in range(2):
        text = _SUFFIX_RE.sub("", text).strip(" .!?,")
    text = re.sub(r"(\d)\s*(?:per ?cent|%)", r"\1%", text)
    return text.strip()
