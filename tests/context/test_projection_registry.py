from dataclasses import dataclass

from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.projection_registry import ContextProjectionRegistry
from agentos.context.xml import XmlElement


def projection(slot: str, owner: str) -> ContextSlotProjection:
    return ContextSlotProjection(
        slot=slot,  # type: ignore[arg-type]
        owner=owner,
        variants=(ProjectionVariant(XmlElement(slot)),),
    )


@dataclass
class RecordingProvider:
    value: ContextSlotProjection | None
    calls: int = 0

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        self.calls += 1
        return () if self.value is None else (self.value,)


def test_registry_preserves_provider_and_projection_order() -> None:
    first = RecordingProvider(projection("declared-schema", "ContextRuntime"))
    second = RecordingProvider(projection("working-state", "ContextRuntime"))

    registry = ContextProjectionRegistry((first, second))

    assert [item.slot for item in registry.projections()] == [
        "declared-schema",
        "working-state",
    ]


def test_registry_reads_authoritative_providers_for_every_request() -> None:
    provider = RecordingProvider(None)
    registry = ContextProjectionRegistry((provider,))

    assert registry.projections() == ()
    provider.value = projection("artifact-catalog", "ArtifactRuntime")
    assert registry.projections() == (provider.value,)
    assert provider.calls == 2


def test_registry_freezes_provider_collection() -> None:
    providers: list[RecordingProvider] = [RecordingProvider(None)]

    registry = ContextProjectionRegistry(providers)
    providers.clear()

    assert len(registry.providers) == 1
