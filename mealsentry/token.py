"""Signed, expiring tokens for the control page link.

The bot sends `/d/<token>` links over Telegram; the token is an HMAC over an expiry
timestamp using a per-install secret stored in the DB (kv). Stateless: any process holding
the secret can verify. Good enough for a single-user tool reachable only over Tailscale.
"""

from __future__ import annotations

import hashlib
import hmac
import time

DEFAULT_TTL = 1800  # 30 minutes


def _sign(secret: bytes, msg: str) -> str:
    return hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(secret: bytes, ttl: int = DEFAULT_TTL) -> str:
    exp = int(time.time()) + ttl
    return f"{exp}.{_sign(secret, str(exp))}"


def verify_token(secret: bytes, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(secret, exp_s)):
        return False
    try:
        return time.time() < int(exp_s)
    except ValueError:
        return False
