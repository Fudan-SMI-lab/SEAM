import json
from pathlib import Path

from core.operator_constraints import preflight_operator_sources


def test_operator_constraints_reject_forbidden_api(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text(
        "import torch\ndef run(x):\n    return torch.erf(x)\n",
        encoding="utf-8",
    )
    result = preflight_operator_sources(
        tmp_path,
        json.dumps({"api_policy": {"forbidden_api": ["torch.erf"]}}),
    )
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.erf"


def test_operator_constraints_allow_generated_custom_op(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text(
        "import torch\ndef run(x):\n"
        "    return torch.ops.cpp_extension_acs.gelu_(x)\n",
        encoding="utf-8",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is True


def test_operator_constraints_reject_unlisted_torch_api(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text(
        "import torch\ndef run(x):\n    return torch.sigmoid(x)\n",
        encoding="utf-8",
    )
    result = preflight_operator_sources(tmp_path)
    assert result["passed"] is False
    assert result["violations"][0]["api"] == "torch.sigmoid"
