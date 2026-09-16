"""
An in-memory sliding-window rate limiter.

hum.log lets anyone comment without an account, which means the endpoints that
cost the most are the ones with nobody's name on them. This is the cheap first
line: a per-IP window held in this process, checked before the request reaches
a service or the database.

It is per-process. The deployment runs a single replica (see
k8s/base/deployment.yaml), so that is the whole of it; scaling out means moving
the counters to Redis, and until then a second replica would double every limit
here. The limits that must survive a restart — how many comments one address
may leave on one post — are counted in the database instead, in
app/services/comment_service.py.
"""

import logging
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_requests: dict[str, list[float]] = defaultdict(list)

# Sweeping only the key being checked leaves keys nobody touches again in
# memory forever, so every so often the whole store gets a pass.
_SWEEP_EVERY_SECONDS = 300
_last_sweep = time.monotonic()
_LONGEST_WINDOW = 3600


def client_ip(request: Request) -> str:
    """The caller's address, trusting the ingress's X-Forwarded-For.

    uvicorn runs with `--forwarded-allow-ips *` behind the cluster ingress, so
    the header is the only place the real address survives. On a direct
    connection there is no header and `request.client` is the truth.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Drop every window that has fully expired (must hold _lock)."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_EVERY_SECONDS:
        return
    _last_sweep = now
    cutoff = now - _LONGEST_WINDOW
    for key in [k for k, stamps in _requests.items() if not stamps or stamps[-1] <= cutoff]:
        del _requests[key]


def allow(key: str, max_requests: int, window_seconds: float) -> bool:
    """Record a request against `key` and say whether it is within the limit."""
    now = time.monotonic()
    with _lock:
        _sweep(now)
        cutoff = now - window_seconds
        stamps = [t for t in _requests[key] if t > cutoff]
        if len(stamps) >= max_requests:
            _requests[key] = stamps
            return False
        stamps.append(now)
        _requests[key] = stamps
        return True


def _limit(request: Request, bucket: str, max_requests: int, window_seconds: int, message: str):
    ip = client_ip(request)
    if not allow(f"{bucket}:{ip}", max_requests, window_seconds):
        logger.warning(f"Rate limit '{bucket}' hit by {ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=message,
            headers={"Retry-After": str(window_seconds)},
        )


# --- The dependencies the routes actually use ------------------------------

def rate_limit_auth(request: Request) -> None:
    """Registration and token refresh: 20 a minute per address."""
    _limit(request, "auth", 20, 60, "Too many requests. Try again in a minute.")


def rate_limit_login(request: Request) -> None:
    """Password attempts: 8 a minute per address."""
    _limit(request, "login", 8, 60, "Too many sign-in attempts. Try again in a minute.")


def rate_limit_reset(request: Request) -> None:
    """Forgotten-password requests: 5 an hour per address.

    Low, because each one sends an email from a real mailbox with a real
    sending quota, and because the endpoint answers identically whether or not
    the address exists — so it is also the endpoint somebody would use to
    enumerate them.
    """
    _limit(request, "reset", 5, 3600, "Too many reset requests. Try again later.")


def rate_limit_comment(request: Request) -> None:
    """Comments: 6 in ten minutes per address, across every post."""
    _limit(request, "comment", 6, 600, "You're commenting very fast. Give it a minute.")


def rate_limit_upload(request: Request) -> None:
    """Image uploads: 60 in ten minutes per address."""
    _limit(request, "upload", 60, 600, "Too many uploads at once. Give it a minute.")


def rate_limit_public(request: Request) -> None:
    """Anonymous reads: 300 a minute per address.

    Generous — this is a blog, and a reader opening ten tabs is a reader, not
    an attack. It is here so a scraper hitting the feed in a loop meets
    something before Postgres does.
    """
    _limit(request, "public", 300, 60, "Slow down a moment.")
