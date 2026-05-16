from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_quickstart_and_architecture_docs_exist() -> None:
    assert (ROOT / "README.md").read_text().strip()
    assert "Quickstart" in (ROOT / "README.md").read_text()
    assert (ROOT / "docs" / "quickstart.md").exists()
    assert (ROOT / "docs" / "architecture.md").exists()


def test_production_hardening_todo_is_checked_off() -> None:
    todo = (ROOT / "docs" / "todo-production-hardening.md").read_text()

    assert "- [ ]" not in todo


def test_required_examples_exist() -> None:
    for name in [
        "streaming_agent.py",
        "multi_agent_dispatch.py",
        "mcp_agent.py",
        "persistent_agent.py",
    ]:
        text = (ROOT / "src" / "agentos" / "examples" / name).read_text()
        assert 'if __name__ == "__main__"' in text
