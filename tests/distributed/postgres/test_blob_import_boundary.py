from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_base_and_blob_protocol_imports_do_not_load_s3_clients() -> None:
    script = """
import json
import sys
sys.path.insert(0, "src")
import agentos
import agentos.distributed.blobs
import agentos.distributed.blobs.protocol
loaded = sorted(
    name for name in sys.modules
    if name == "aioboto3"
    or name.startswith("aioboto3.")
    or name == "aiobotocore"
    or name.startswith("aiobotocore.")
)
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
