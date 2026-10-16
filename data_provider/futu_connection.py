"""Bounded connection setup for the optional Futu OpenD quote gateway."""

import socket
from typing import Any, Callable


def futu_opend_reachable(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Check the configured TCP endpoint before invoking the reconnecting SDK."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_futu_quote_context(
    factory: Callable[..., Any], host: str, port: int, *, query_timeout: float = 5.0
) -> Any:
    """Open a quote context without the SDK's unbounded synchronous retry loop."""
    if not futu_opend_reachable(host, port):
        raise ConnectionError(f"Futu OpenD is not listening at {host}:{port}")
    context = factory(host=host, port=port, is_async_connect=True)
    try:
        context.set_sync_query_connect_timeout(query_timeout)
    except Exception:
        context.close()
        raise
    return context
