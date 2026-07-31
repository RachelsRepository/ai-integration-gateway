"""HMAC request signing and verification."""

from __future__ import annotations

import hashlib
import hmac
import time


class RequestSigner:
    """Signs and verifies request bodies with HMAC-SHA256."""

    def __init__(self, secret: str, *, max_skew_seconds: int = 300) -> None:
        """Initialise the signer.

        Args:
            secret: Shared signing secret.
            max_skew_seconds: Maximum accepted clock skew for timestamps.
        """
        self._secret = secret.encode("utf-8")
        self._max_skew = max_skew_seconds

    def sign(self, body: bytes, *, timestamp: int | None = None) -> tuple[str, str]:
        """Sign a request body.

        Args:
            body: Raw request body.
            timestamp: Unix timestamp; defaults to now.

        Returns:
            A tuple of ``(timestamp, signature_hex)``.
        """
        ts = str(timestamp if timestamp is not None else int(time.time()))
        digest = hmac.new(self._secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return ts, digest

    def verify(self, body: bytes, *, timestamp: str, signature: str) -> bool:
        """Verify a signed request.

        Args:
            body: Raw request body.
            timestamp: Timestamp header value.
            signature: Signature header value.

        Returns:
            ``True`` when the signature is valid and the timestamp is fresh.
        """
        try:
            ts = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - ts) > self._max_skew:
            return False
        expected = hmac.new(
            self._secret, f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


__all__ = ["RequestSigner"]
