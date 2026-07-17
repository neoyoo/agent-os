from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from agentos.context.models import ContextSlotProjection
from agentos.context.projection import project_context_state
from agentos.context.state import ContextState


class ContextProjectionProvider(Protocol):
    """Provide current authoritative context slot projections."""

    def projections(self) -> tuple[ContextSlotProjection, ...]: ...


class ContextStateSource(Protocol):
    def snapshot(self) -> ContextState: ...


@dataclass(frozen=True, slots=True)
class ContextRuntimeProjectionProvider:
    source: ContextStateSource

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        return project_context_state(self.source.snapshot())


@dataclass(frozen=True, slots=True, init=False)
class ContextProjectionRegistry:
    """Aggregate context projections in stable provider order."""

    providers: tuple[ContextProjectionProvider, ...]

    def __init__(self, providers: Iterable[ContextProjectionProvider] = ()) -> None:
        object.__setattr__(self, "providers", tuple(providers))

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        return tuple(
            projection
            for provider in self.providers
            for projection in provider.projections()
        )


__all__ = [
    "ContextProjectionProvider",
    "ContextProjectionRegistry",
    "ContextRuntimeProjectionProvider",
]
