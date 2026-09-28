"""Generate booking-code variants by mutating the trailing characters.

Progressive strategy matching the requested flow: first vary the last 1
character, then the last 2, then the last 3. Codes are case-sensitive base62,
so each mutated position ranges over the full charset. A ``seen`` set makes the
stream duplicate-free, and the original seed is never re-yielded.
"""
from __future__ import annotations

from itertools import product
from typing import Iterator


def mutate(seed: str, depth: int, charset: str) -> Iterator[str]:
    """Yield unique variants of ``seed`` mutating its last ``depth`` chars.

    Depth is applied progressively: stage 1 changes only the final char, stage 2
    the final two, stage 3 the final three. Variants a shallower stage already
    produced are suppressed via ``seen``, so every code is yielded at most once
    and the seed itself is never yielded.
    """
    seed = seed.strip()
    depth = max(1, min(depth, len(seed)))
    seen: set[str] = {seed}

    for stage in range(1, depth + 1):
        prefix = seed[:-stage]
        for combo in product(charset, repeat=stage):
            candidate = prefix + "".join(combo)
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
