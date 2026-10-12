"""Per-client token-bucket rate limiting.

Each client IP has a bucket per tier. A bucket holds up to `rate` tokens and
refills continuously at `rate` per minute; each request takes one token.
Empty bucket -> HTTP 429 with a Retry-After header.

Client IP: by default the direct peer address. Behind a reverse proxy (e.g.
Render) every request comes from the proxy, so set TRUSTED_PROXY_HOPS=1 and
we take the address the proxy appended to X-Forwarded-For. We read from the
right-hand end because the left-hand entries are whatever the client chose to
send and can be spoofed to dodge the limit.

Limitation: buckets live in this process's memory. With several instances
each keeps its own counts; a shared store (Redis) would fix that.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from starlette.requests import Request


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, per_minute: int, max_clients: int = 50_000):
        self.rate = float(per_minute)
        self.max_clients = max_clients
        self._buckets: dict[str, Bucket] = {}

    def check(self, client: str) -> float:
        """Take a token. Returns 0 if allowed, else seconds until one is available."""
        now = time.monotonic()
        b = self._buckets.get(client)
        if b is None:
            if len(self._buckets) >= self.max_clients:
                self._evict(now)
            b = self._buckets[client] = Bucket(self.rate, now)
        b.tokens = min(self.rate, b.tokens + (now - b.updated) * self.rate / 60.0)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return 0.0
        return (1 - b.tokens) * 60.0 / self.rate

    def _evict(self, now: float) -> None:
        # Buckets idle for over a minute are full again; forgetting them is harmless.
        self._buckets = {k: v for k, v in self._buckets.items() if now - v.updated < 60}


def client_ip(request: Request, trusted_hops: int) -> str:
    if trusted_hops > 0:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"
