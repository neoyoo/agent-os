from agentos.examples.mcp_agent import main as mcp_main
from agentos.examples.persistent_agent import main as persistent_main
from agentos.examples.streaming_agent import main as streaming_main


def test_streaming_agent_example_runs(capsys) -> None:
    streaming_main()

    assert "TurnStreamCompleted" in capsys.readouterr().out


def test_persistent_agent_example_runs(capsys) -> None:
    persistent_main()

    assert capsys.readouterr().out.strip() == "persisted"


def test_mcp_agent_example_runs(capsys) -> None:
    mcp_main()

    assert capsys.readouterr().out.strip() == "mcp-ready"
