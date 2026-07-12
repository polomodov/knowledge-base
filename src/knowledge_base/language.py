from __future__ import annotations

# Basic Cyrillic (incl. ё/Ё) and Latin letter ranges for a lightweight script heuristic.
_CYRILLIC_RANGES = ((0x0400, 0x04FF),)
_LATIN_RANGES = ((ord("A"), ord("Z")), (ord("a"), ord("z")))


def detect_language(text: str) -> str:
    """Return ``ru``, ``en``, or ``unknown`` from Cyrillic vs Latin letter ratio.

    Counts alphabetic characters only. Majority Cyrillic → ``ru``, majority Latin →
    ``en``, empty / no letters / tie → ``unknown``. Intentionally heuristic — not a
    full language-ID model.
    """
    if not text or not text.strip():
        return "unknown"

    cyrillic = 0
    latin = 0
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        if any(start <= code <= end for start, end in _CYRILLIC_RANGES):
            cyrillic += 1
        elif any(start <= code <= end for start, end in _LATIN_RANGES):
            latin += 1

    if cyrillic == 0 and latin == 0:
        return "unknown"
    if cyrillic > latin:
        return "ru"
    if latin > cyrillic:
        return "en"
    return "unknown"
