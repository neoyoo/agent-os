import agentos


def test_context_kernel_public_types_are_importable() -> None:
    from agentos.context import (
        ContextRenderer,
        ContextSnapshot,
        ContextSnapshotRenderer,
        SystemEnvelope,
    )

    assert ContextRenderer is not None
    assert ContextSnapshot is not None
    assert ContextSnapshotRenderer is not None
    assert SystemEnvelope is not None


def test_context_kernel_types_are_not_exported_from_root_facade() -> None:
    assert not hasattr(agentos, "ContextSnapshot")
    assert "ContextRenderer" not in agentos.__all__
    assert "ContextSnapshot" not in agentos.__all__
    assert "ContextSnapshotRenderer" not in agentos.__all__
    assert "SystemEnvelope" not in agentos.__all__
