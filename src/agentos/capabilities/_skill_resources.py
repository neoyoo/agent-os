import mimetypes
from pathlib import Path


def _safe_resource_path(root: Path, resource_path: str) -> Path | None:
    normalized = Path(resource_path)
    if normalized.is_absolute() or any(part == ".." for part in normalized.parts):
        return None
    root_resolved = root.resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def _guess_mime_type(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return "text/markdown"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "text/plain"
