"""Shared-secret authentication (constant-time, fail closed)."""

from __future__ import annotations

import hmac


def token_valid(provided: str | None, expected: str) -> bool:
    """Return True only if ``expected`` is configured and ``provided`` matches it.

    Uses :func:`hmac.compare_digest` so timing does not leak the token.
    An empty configured token rejects everything.
    """
    if not expected or provided is None:
        # Still do a comparison so the empty-config path is not trivially faster.
        hmac.compare_digest(b"x", b"y")
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
