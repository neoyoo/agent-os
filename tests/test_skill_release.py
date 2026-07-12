from pathlib import Path

import pytest


def _write_skill(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_build_skill_release_manifest_hashes_files_deterministically(
    tmp_path: Path,
) -> None:
    from agentos.skills import build_skill_release_manifest

    skill_dir = tmp_path / "agent-os"
    _write_skill(
        skill_dir,
        {
            "SKILL.md": "---\nname: agent-os-sdk\n---\n# agent-os\n",
            "modules/quick-start.md": "# Quick Start\n",
            "flow/01-requirements.md": "# Requirements\n",
        },
    )

    manifest = build_skill_release_manifest(
        skill_dir,
        version="2026.06.16",
        source="repo://agent-os",
    )
    again = build_skill_release_manifest(
        skill_dir,
        version="2026.06.16",
        source="repo://agent-os",
    )

    assert manifest.skill_name == "agent-os"
    assert manifest.version == "2026.06.16"
    assert manifest.source == "repo://agent-os"
    assert manifest.file_count == 3
    assert manifest.files == again.files
    assert manifest.manifest_hash == again.manifest_hash
    assert [entry.path for entry in manifest.files] == [
        "SKILL.md",
        "flow/01-requirements.md",
        "modules/quick-start.md",
    ]
    assert all(len(entry.sha256) == 64 for entry in manifest.files)
    assert manifest.as_dict()["manifest_hash"] == manifest.manifest_hash


def test_skill_release_manifest_rejects_invalid_skill_directories(
    tmp_path: Path,
) -> None:
    from agentos.skills import build_skill_release_manifest

    missing = tmp_path / "missing"
    no_skill_file = tmp_path / "no-skill-file"
    no_skill_file.mkdir()

    with pytest.raises(FileNotFoundError, match="skill_dir"):
        build_skill_release_manifest(
            missing,
            version="2026.06.16",
            source="repo://agent-os",
        )
    with pytest.raises(FileNotFoundError, match="SKILL.md"):
        build_skill_release_manifest(
            no_skill_file,
            version="2026.06.16",
            source="repo://agent-os",
        )
    with pytest.raises(ValueError, match="version"):
        build_skill_release_manifest(
            no_skill_file,
            version=" ",
            source="repo://agent-os",
        )
    with pytest.raises(ValueError, match="source"):
        build_skill_release_manifest(
            no_skill_file,
            version="2026.06.16",
            source=" ",
        )


def test_compare_skill_release_manifests_reports_file_and_version_drift(
    tmp_path: Path,
) -> None:
    from agentos.skills import (
        build_skill_release_manifest,
        compare_skill_release_manifests,
    )

    expected_dir = tmp_path / "expected" / "agent-os"
    actual_dir = tmp_path / "actual" / "agent-os"
    _write_skill(
        expected_dir,
        {
            "SKILL.md": "---\nname: agent-os-sdk\n---\n# agent-os\n",
            "modules/quick-start.md": "# Quick Start\n",
            "modules/agent-forms.md": "# Agent Forms\n",
        },
    )
    _write_skill(
        actual_dir,
        {
            "SKILL.md": "---\nname: agent-os-sdk\n---\n# agent-os edited\n",
            "modules/quick-start.md": "# Quick Start\n",
            "modules/extra-local.md": "# Local Edit\n",
        },
    )

    expected = build_skill_release_manifest(
        expected_dir,
        version="2026.06.16",
        source="repo://agent-os",
    )
    actual = build_skill_release_manifest(
        actual_dir,
        version="2026.06.15",
        source="user://agent-os",
    )

    report = compare_skill_release_manifests(expected, actual)

    assert report.ready is False
    assert report.status == "drift"
    assert report.version_match is False
    assert report.expected_version == "2026.06.16"
    assert report.actual_version == "2026.06.15"
    assert report.missing_files == ("modules/agent-forms.md",)
    assert report.extra_files == ("modules/extra-local.md",)
    assert report.changed_files == ("SKILL.md",)
    assert report.as_dict()["missing_files"] == ("modules/agent-forms.md",)


def test_compare_skill_release_manifests_marks_matching_copy_ready(
    tmp_path: Path,
) -> None:
    from agentos.skills import (
        build_skill_release_manifest,
        compare_skill_release_manifests,
    )

    expected_dir = tmp_path / "expected" / "agent-os"
    actual_dir = tmp_path / "actual" / "agent-os"
    files = {
        "SKILL.md": "---\nname: agent-os-sdk\n---\n# agent-os\n",
        "modules/quick-start.md": "# Quick Start\n",
    }
    _write_skill(expected_dir, files)
    _write_skill(actual_dir, files)

    expected = build_skill_release_manifest(
        expected_dir,
        version="2026.06.16",
        source="repo://agent-os",
    )
    actual = build_skill_release_manifest(
        actual_dir,
        version="2026.06.16",
        source="user://agent-os",
    )

    report = compare_skill_release_manifests(expected, actual)

    assert report.ready is True
    assert report.status == "ok"
    assert report.version_match is True
    assert report.missing_files == ()
    assert report.extra_files == ()
    assert report.changed_files == ()
