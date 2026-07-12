import importlib

from agentos.context import ContextRenderer, SystemEnvelope


def test_default_context_renderer_fixture_builds_explicit_system_envelope() -> None:
    fixtures = importlib.import_module("tests._context_protocol_fixtures")
    renderer = fixtures.default_context_renderer()

    assert isinstance(renderer, ContextRenderer)
    envelope = renderer.render()
    assert isinstance(envelope, SystemEnvelope)
    assert "# Runtime Contract" in envelope.text
