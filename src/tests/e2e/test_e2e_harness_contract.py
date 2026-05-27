# pyright: reportUnknownMemberType=false

from __future__ import annotations

import inspect
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e import e2e_test


def test_direct_e2e_harness_passes_phase3_contract_to_repair_loop() -> None:
    source = inspect.getsource(e2e_test.run_e2e)

    assert 'phase3_output = phase_outputs.get("phase_3_entry_script")' in source
    assert 'phase3_contract = dict(phase3_output) if isinstance(phase3_output, dict) else None' in source
    assert 'phase3_contract=phase3_contract' in source
