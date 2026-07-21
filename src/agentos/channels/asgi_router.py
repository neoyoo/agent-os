from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RouteMatch:
    operation: str
    parameters: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


class AsgiRouter:
    """Pure method/path matcher for distributed HTTP and A2A operations."""

    def match(self, method: str, path: str) -> RouteMatch | None:
        if method == "POST" and path == "/a2a":
            return RouteMatch("a2a", {})
        if (
            type(method) is not str
            or type(path) is not str
            or not path.startswith("/")
            or path == "/"
        ):
            return None
        segments = path[1:].split("/")
        if any(not segment for segment in segments):
            return None
        if len(segments) < 4 or segments[:2] != ["v1", "sessions"]:
            return None
        session_id = segments[2]
        if segments[3] == "runs":
            return _run_route(method, segments, session_id)
        if segments[3] == "artifacts":
            return _artifact_route(method, segments, session_id)
        return None


def _run_route(
    method: str,
    segments: list[str],
    session_id: str,
) -> RouteMatch | None:
    if method == "POST" and len(segments) == 4:
        return RouteMatch("submit_run", {"session_id": session_id})
    if len(segments) < 5:
        return None
    run_id = segments[4]
    parameters = {"session_id": session_id, "run_id": run_id}
    if method == "GET" and len(segments) == 5:
        return RouteMatch("query_run", parameters)
    if method == "POST" and segments[5:] == ["commands"]:
        return RouteMatch("submit_command", parameters)
    if method == "GET" and segments[5:] == ["events"]:
        return RouteMatch("subscribe_run", parameters)
    return None


def _artifact_route(
    method: str,
    segments: list[str],
    session_id: str,
) -> RouteMatch | None:
    if len(segments) == 4:
        operation = {"GET": "list_artifacts", "POST": "upload_artifact"}.get(method)
        return None if operation is None else RouteMatch(operation, {"session_id": session_id})
    if len(segments) != 5:
        return None
    operation = {"GET": "read_artifact", "DELETE": "delete_artifact"}.get(method)
    if operation is None:
        return None
    return RouteMatch(
        operation,
        {"artifact_id": segments[4], "session_id": session_id},
    )


__all__ = ["AsgiRouter", "RouteMatch"]
