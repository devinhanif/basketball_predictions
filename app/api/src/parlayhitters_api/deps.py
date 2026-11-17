"""What every store-backed route depends on: the one cached store, and whether a request came
directly from this machine (the private desk gate)."""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import Depends, Request

from .store import AnyStore, load_store, store_key

_cache: dict[str, Any] = {}
_load_lock = threading.Lock()


def get_store() -> AnyStore:
    """The data behind every route: the verified export if one is configured, else the fixtures.
    Reloaded when a new export lands (the manifest is replaced last), without a restart."""
    key = store_key()
    if _cache.get("key") != key or "store" not in _cache:
        # One load at a time: a cold start answers its first requests together, and without the
        # lock each of them would load the whole export again (about 15 s of CPU apiece).
        with _load_lock:
            if _cache.get("key") != key or "store" not in _cache:
                _cache["store"] = load_store()  # ExportRefused for an export we cannot vouch for
                _cache["key"] = key
    store: AnyStore = _cache["store"]
    return store


def reset_store() -> None:
    _cache.clear()


StoreDep = Annotated[AnyStore, Depends(get_store)]


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-forwarded-host", "forwarded")


def _host_name(host: str) -> str:
    h = host.strip().lower()
    if h.startswith("["):
        return h[1 : h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


_LOOPBACK_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _is_local_request(request: Request) -> bool:
    """True only for a request that reached this machine directly, not via a tunnel or proxy: the
    connecting peer is loopback (a Host header alone is the client's word, and a server started
    on 0.0.0.0 would otherwise take a LAN request that claims Host: localhost), the Host names
    this machine, and no proxy or tunnel header is present."""
    peer = request.client.host if request.client is not None else ""
    if peer not in _LOOPBACK_PEERS:
        return False
    if any(request.headers.get(h) for h in _PROXY_HEADERS):
        return False
    return _host_name(request.headers.get("host", "")) in _LOCAL_HOSTS
