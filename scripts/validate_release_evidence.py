from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from agentos.release import validate_release_candidate_evidence_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an AgentOS release candidate evidence manifest.",
    )
    parser.add_argument("--manifest", required=True, help="Evidence manifest path.")
    parser.add_argument("--branch", required=True, help="Expected source branch.")
    parser.add_argument("--commit", required=True, help="Expected source commit.")
    parser.add_argument("--version", required=True, help="Expected SDK version.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(
            f"release evidence manifest missing: {manifest_path}",
            file=sys.stderr,
        )
        return 2

    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except UnicodeError:
        print(
            "release evidence manifest unreadable: invalid UTF-8",
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError:
        print(
            "release evidence manifest unreadable: invalid JSON",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"release evidence manifest unreadable: {exc}", file=sys.stderr)
        return 2
    if not isinstance(manifest_value, Mapping):
        print(
            "release evidence manifest must be a JSON object",
            file=sys.stderr,
        )
        return 2
    manifest = dict(manifest_value)

    report = validate_release_candidate_evidence_manifest(
        manifest,
        expected_branch=args.branch,
        expected_commit=args.commit,
        expected_version=args.version,
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
