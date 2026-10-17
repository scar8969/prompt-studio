"""The adapter registry (spec §24.5): one place that maps a connection to an adapter.

Registration is explicit and ordered. An unknown provider name gets the
OpenAI-compatible adapter, because that shape is what the BYOK flow targets — but it
does not get any special behaviour it was not configured for.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from .base import Adapter

_registry: Dict[str, Type[Adapter]] = {}
_order: List[str] = []


def register(name: str, adapter_cls: Type[Adapter], *, default: bool = False) -> None:
    _registry[name] = adapter_cls
    if name not in _order:
        _order.append(name)
    if default:
        _order.remove(name)
        _order.insert(0, name)


def get(name: str = "") -> Adapter:
    """An adapter instance for a provider name. Falls back to the first registered."""
    key = (name or "").strip().lower()
    if key in _registry:
        return _registry[key]()
    if not _order:
        raise RuntimeError("no adapters registered")
    return _registry[_order[0]]()


def names() -> List[str]:
    return list(_order)


def known(name: str) -> bool:
    return (name or "").strip().lower() in _registry


def install_defaults() -> None:
    """Register the built-in adapters. Idempotent."""
    if _registry:
        return
    from .openai_compatible import OpenAICompatibleAdapter

    try:
        from .mock import MockAdapter
    except ImportError:  # pragma: no cover
        MockAdapter = None  # type: ignore

    if MockAdapter is not None:
        register("mock", MockAdapter)
    register("openai-compatible", OpenAICompatibleAdapter, default=True)
    register("openai", OpenAICompatibleAdapter)
    register("custom", OpenAICompatibleAdapter)
