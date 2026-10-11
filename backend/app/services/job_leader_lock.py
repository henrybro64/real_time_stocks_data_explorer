"""Redis leader locks shared by background jobs (#276).

When Redis is unavailable, the caller may run locally (single-process default)
or skip (multi-replica fail-closed). Quote refresher already fails closed when
Redis is configured but unreachable; other jobs follow the same pattern when
``fail_closed`` is true.
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from threading import Lock
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""

# Redis tokens this process holds, so shutdown can hand the windows over (#292).
_held_lock = Lock()
_held_tokens: dict[str, str] = {}
# Which jobs the current thread's last tick skipped for leadership (#292).
_tick_state = threading.local()


def _skipped_jobs() -> set[str]:
    skipped = getattr(_tick_state, "skipped", None)
    if skipped is None:
        skipped = set()
        _tick_state.skipped = skipped
    return skipped


def try_acquire_job_leadership(
    job_name: str,
    *,
    ttl_seconds: int = 120,
    fail_closed: bool = True,
) -> str | None:
    """Return a leadership token when this process should run the job tick."""
    if not settings.redis_url.strip():
        # No Redis: single-node mode; every process may run (local default).
        return f"local-{uuid4().hex}"

    from app.services.quote_redis_cache import run_redis

    token = uuid4().hex
    key = f"job:leader:v1:{job_name}"

    def _acquire(client):
        return bool(client.set(key, token, nx=True, ex=int(ttl_seconds)))

    acquired = run_redis(f"job leader acquire {job_name}", _acquire, kind="write")
    if acquired:
        with _held_lock:
            _held_tokens[job_name] = token
        return token
    if acquired is None and not fail_closed:
        return f"local-{uuid4().hex}"
    if acquired is None and fail_closed:
        logger.info(
            "Job leadership skipped; Redis unavailable",
            extra={"event": "job.leader.skipped_redis", "job_name": job_name},
        )
    _skipped_jobs().add(job_name)
    return None


_daily_lock = Lock()
_daily_markers: dict[str, str] = {}


def clear_daily_job_markers_for_tests() -> None:
    with _daily_lock:
        _daily_markers.clear()


def daily_job_already_ran(job_name: str, day_iso: str) -> bool:
    """True when this calendar day was already claimed in Redis or this process."""
    if not settings.redis_url.strip():
        with _daily_lock:
            return _daily_markers.get(job_name) == day_iso
    from app.services.quote_redis_cache import run_redis

    key = f"job:daily:v1:{job_name}"
    current = run_redis(
        f"job daily read {job_name}",
        lambda client: client.get(key),
        kind="read",
    )
    if current is None:
        with _daily_lock:
            return _daily_markers.get(job_name) == day_iso
    text = current.decode("utf-8") if isinstance(current, bytes) else str(current)
    return text == day_iso


def remember_daily_job(job_name: str, day_iso: str) -> None:
    """Record that ``job_name`` finished ``day_iso`` so another worker skips it."""
    with _daily_lock:
        _daily_markers[job_name] = day_iso
    if not settings.redis_url.strip():
        return
    from app.services.quote_redis_cache import run_redis

    key = f"job:daily:v1:{job_name}"
    run_redis(
        f"job daily write {job_name}",
        lambda client: client.set(key, day_iso, ex=36 * 3600),
        kind="write",
    )


def release_job_leadership(job_name: str, token: str | None) -> None:
    if token is None or token.startswith("local-"):
        return
    with _held_lock:
        if _held_tokens.get(job_name) == token:
            _held_tokens.pop(job_name, None)
    if not settings.redis_url.strip():
        return
    from app.services.quote_redis_cache import run_redis

    key = f"job:leader:v1:{job_name}"

    def _release(client):
        client.eval(_RELEASE_SCRIPT, 1, key, token)

    run_redis(f"job leader release {job_name}", _release, kind="write")


def release_all_job_leadership() -> None:
    """Release every leader key this process holds; others' keys are untouched (#292)."""
    with _held_lock:
        held = list(_held_tokens.items())
    for job_name, token in held:
        release_job_leadership(job_name, token)


def leadership_skipped(job_name: str) -> bool:
    """True when this thread's last ``run_leader_tick`` for the job lacked leadership."""
    return job_name in _skipped_jobs()


def run_leader_tick(job_name: str, fn: Callable[[], object]) -> object:
    """Run one sampler tick; ``JOB_TICK_INCOMPLETE`` when skipped for leadership (#292).

    The service method keeps its own return type (capture endpoints call it too);
    only the sampler maps a skip, so ``/health`` does not record it as a success.
    """
    from app.jobs.health import JOB_TICK_INCOMPLETE

    _skipped_jobs().discard(job_name)
    result = fn()
    return JOB_TICK_INCOMPLETE if leadership_skipped(job_name) else result


def leader_retry_wait_seconds(poll_seconds: float) -> float:
    """Short jittered wait before retrying leadership, capped at the poll (#292)."""
    retry = float(settings.job_leader_retry_seconds)
    return min(float(poll_seconds), retry + random.uniform(0.0, retry * 0.2))


def next_tick_wait_seconds(job_name: str, poll_seconds: float) -> float:
    """Full poll after a tick that ran; a short retry after a leadership skip (#292)."""
    if leadership_skipped(job_name):
        return leader_retry_wait_seconds(poll_seconds)
    return float(poll_seconds)


def clear_held_job_leadership_for_tests() -> None:
    with _held_lock:
        _held_tokens.clear()
    _skipped_jobs().clear()
