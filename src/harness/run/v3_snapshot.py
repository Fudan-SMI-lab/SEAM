from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

from core.compat import SLOTS_KWARG
from core.run_manifest import RunManifestError
from core.run_manifest_paths import inspect_real_tree, read_real_tree_file

from .models import SidecarWriteError
from .sidecars import write_json_text

_EXCLUDED_SNAPSHOT_DIRS: Final = frozenset(
    {".git", ".sm-artifacts", ".venv", "__pycache__"}
)


@dataclass(frozen=True, **SLOTS_KWARG)
class SnapshotResult:
    path: str
    file_count: int


def persist_python_snapshot(project_dir: Path, output_path: Path) -> SnapshotResult:
    snapshot: dict[str, dict[str, str]] = {}
    try:
        tree = inspect_real_tree(
            project_dir, project_dir.parent, budget_suffixes=frozenset({".py"})
        )
        for identity in tree.files:
            relative_path = identity.path.relative_to(tree.root.path)
            if identity.path.suffix != ".py" or any(
                part in _EXCLUDED_SNAPSHOT_DIRS for part in relative_path.parts
            ):
                continue
            raw = read_real_tree_file(tree, identity)
            snapshot[str(relative_path)] = {
                "sha256": sha256(raw).hexdigest(),
                "content": raw.decode("utf-8"),
            }
    except (RunManifestError, UnicodeError) as exc:
        raise SidecarWriteError(str(project_dir), str(exc)) from exc
    serialized = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str)
    return SnapshotResult(
        path=write_json_text(output_path, serialized),
        file_count=len(snapshot),
    )
