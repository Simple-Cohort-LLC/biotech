"""Shared HTTP session: retries, backoff, and a global rate limit."""

from __future__ import annotations

import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

_lock = threading.Lock()
_last_request_at = 0.0

# SEC allows 10 requests/second. We stay well under it for every host, since no
# source here is large enough to need the throughput.
MIN_INTERVAL_SECONDS = 0.15


def _throttle() -> None:
    global _last_request_at
    with _lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < MIN_INTERVAL_SECONDS:
            time.sleep(MIN_INTERVAL_SECONDS - elapsed)
        _last_request_at = time.monotonic()


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    _throttle()
    kwargs.setdefault("timeout", 45)
    return session.get(url, **kwargs)


def post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    _throttle()
    kwargs.setdefault("timeout", 45)
    return session.post(url, **kwargs)
