"""Workflow generation and Phase-2 venv response for the direct-seal lifecycle."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class VenvFingerprint(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid"
    )
    sys_prefix: str
    sys_executable: str
    packages: list[str]


_FP_SCRIPT = (
    "import hashlib, importlib.metadata, json, sys\n"
    "packages = sorted(\n"
    '    f"{name}=={distribution.version}"\n'
    "    for distribution in importlib.metadata.distributions()\n"
    '    for name in (distribution.metadata.get("Name"),)\n'
    "    if name\n"
    ")\n"
    "print(json.dumps({\n"
    '    "sys_prefix": sys.prefix,\n'
    '    "sys_executable": sys.executable,\n'
    '    "packages": packages,\n'
    "}))\n"
)


def venv_phase2_response(venv_python: str) -> str:
    result = subprocess.run(
        [venv_python, "-c", _FP_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )
    fp = VenvFingerprint.model_validate_json(result.stdout)
    return json.dumps(
        {
            "env_type": "venv",
            "venv_path": fp.sys_prefix,
            "python_path": fp.sys_executable,
            "installed_packages": fp.packages,
        }
    )


def write_workflow(
    root: Path, fail_point: str, venv_python: str
) -> Path:
    for name, is_fail in (
        ("m", fail_point == "before5"),
        ("v", fail_point == "at5"),
        ("rp", fail_point == "after5"),
    ):
        if is_fail:
            script = f"""import os, sys
_d = os.path.dirname(os.path.abspath(sys.argv[0]))
_m = os.path.join(_d, ".fo_{name}")
if os.path.exists(_m):
    sys.exit(0)
open(_m, 'w').close()
sys.exit(1)
"""
            _ = (root / f"{name}.py").write_text(script, encoding="utf-8")
        else:
            _ = (root / f"{name}.py").write_text(
                "import sys\nsys.exit(0)\n", encoding="utf-8"
            )
    mc = json.dumps(f'"{venv_python}" "{root / "m.py"}"')
    rc = json.dumps(f'"{venv_python}" "{root / "rp.py"}"')
    vc = json.dumps(f'"{venv_python}" "{root / "v.py"}"')
    y = (
        "name: dsc\nversion: '1.0'\nglobals:\n  review_fail_closed: true\n"
        "experience:\n  enabled: false\nphases:\n"
        "  - id: phase_2_venv_create\n    type: llm\n"
        "    agent: main_engineer\n"
        "    prompt_template: phase_2_venv_create\n"
        f"    transitions: {{on_success: phase_4_migrate}}\n"
        "  - id: phase_4_migrate\n    type: loop\n    sub_workflow: gl\n"
        f"    input_mapping:\n      entry_script: {mc}\n"
        f"      project_dir: \"${{PROJECT_DIR}}\"\n"
        f"    transitions: {{on_success: phase_5_validation, on_failure: failed}}\n"
        "  - id: phase_5_validation\n    type: loop\n    sub_workflow: gl\n"
        f"    input_mapping:\n      entry_script: {vc}\n"
        f"      project_dir: \"${{PROJECT_DIR}}\"\n"
        f"    transitions: {{on_success: phase_6_report, on_failure: failed}}\n"
        "  - id: phase_6_report\n    type: loop\n    sub_workflow: gl\n"
        f"    input_mapping:\n      entry_script: {rc}\n"
        f"      project_dir: \"${{PROJECT_DIR}}\"\n"
        f"    transitions: {{on_success: phase_7_followup, on_failure: failed}}\n"
        "  - id: phase_7_followup\n    type: llm\n"
        "    agent: main_engineer\n"
        "    prompt_template: phase_2_venv_create\n"
        f"    transitions: {{on_success: complete}}\n"
        "terminals: [complete, failed]\nsub_workflows:\n"
        "  gl:\n    id: gl\n    type: loop\n    max_iterations: 1\n"
        "    review_gate_enabled: false\n"
        "    stop_conditions:\n      - condition: \"$.script_exit_code == 0\"\n"
        "        status: success\n    phases:\n"
        "      - id: run_entry_script\n        type: shell\n"
        "        command: \"${loop_vars.entry_script}\"\n"
        "        cwd: \"${loop_vars.project_dir}\"\n        on_failure: break\n"
    )
    p = root / "w.yaml"
    _ = p.write_text(y, encoding="utf-8")
    return p
