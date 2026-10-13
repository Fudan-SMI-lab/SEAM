import json
from pathlib import Path

from core.operator_constraints import preflight_operator_sources


def test_operator_constraints_reject_forbidden_api(tmp_path: Path) -> None:
    _write_solution(tmp_path, "import torch\ndef run(x):\n    return torch.erf(x)\n")
    result = preflight_operator_sources(
        tmp_path,
        json.dumps({"api_policy": {"forbidden_api": ["torch.erf"]}}),
    )
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.erf"


def test_operator_constraints_allow_generated_custom_op(tmp_path: Path) -> None:
    _write_solution(
        tmp_path,
        "import torch\ndef run(x):\n"
        "    return torch.ops.cpp_extension_acs.gelu_(x)\n",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is True


def test_operator_constraints_reject_unlisted_torch_api(tmp_path: Path) -> None:
    _write_solution(tmp_path, "import torch\ndef run(x):\n    return torch.sigmoid(x)\n")
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.sigmoid"


def test_operator_preflight_does_not_scan_reference_source(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "import torch\ndef run(x):\n    return torch.erf(x)\n", encoding="utf-8"
    )
    _write_solution(
        tmp_path,
        "import torch\ndef run(x):\n"
        "    return torch.ops.cpp_extension_acs.gelu_(x)\n",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is True
    assert result["files_scanned"] == 1


def test_operator_constraints_resolve_torch_aliases(tmp_path: Path) -> None:
    _write_solution(
        tmp_path,
        "import torch as th\ndef run(x):\n    return th.sigmoid(x)\n",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.sigmoid"


def test_operator_constraints_reject_getattr_bypass(tmp_path: Path) -> None:
    _write_solution(
        tmp_path,
        "import torch\ndef run(x):\n    return getattr(torch, 'sigmoid')(x)\n",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.sigmoid"


def _write_solution(task_dir: Path, source: str) -> None:
    solution = {
        "sources": [{"path": "adapter.py", "content": source}],
    }
    (task_dir / "solution.json").write_text(
        json.dumps(solution), encoding="utf-8"
    )
