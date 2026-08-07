from __future__ import annotations

# pyright: reportImplicitRelativeImport=false

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# pylint: disable=wrong-import-position
from src.training import runner


def prepare_data(project_root: Path | None = None) -> Path:
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    stats_path = data_dir / "prepared_stats.yaml"
    payload = {
        "scale": 0.95,
        "bias": 0.02,
        "description": "Synthetic normalization stats for the smoke test project.",
    }
    with stats_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=True)
    return stats_path


def prepare_and_launch() -> int:
    _ = prepare_data(PROJECT_ROOT)
    return runner.main()
