from collections.abc import Callable
from pathlib import Path
from typing import cast

from core.routes import normalize_serving_phase1_surface, normalize_serving_phase3_contract
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_project_analysis import validate as validate_project_analysis
from validators.serving_validator import validate_serving_final_gate


def _dict_value(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _string_list(value: object) -> list[str]:
    assert isinstance(value, list)
    items = cast(list[object], value)
    assert all(isinstance(item, str) for item in items)
    return cast(list[str], items)


def _phase1_surface(route: str = "vllm_serving") -> dict[str, object]:
    return {
        "project_dir": "/tmp/project",
        "dependencies": ["torch", "vllm"],
        "cuda_detected": True,
        "entry_script": "serve.py",
        "migration_route": route,
        "serving_runtime_surface": {
            "launch_command": "python serve.py --model tiny",
            "launch_evidence": ["serve.py starts the project API"],
            "project_demo_or_test_evidence": ["tests call the API endpoint"],
            "project_test_files": ["tests/test_api.py"],
            "expected_outputs": ["200 OK"],
            "required_runtime_env": ["serving framework installed"],
            "readiness_probe": {"type": "http", "url": "/health"},
            "request_validation": {"type": "http", "url": "/generate"},
            "unresolved_source_groups": [],
        },
    }


def _normalized_contract(project_root: Path, route: str = "vllm_serving") -> dict[str, object]:
    project_root.mkdir(parents=True, exist_ok=True)
    phase1 = _phase1_surface(route)
    normalize_serving_phase1_surface(phase1)
    contract: dict[str, object] = {"project_dir": str(project_root)}
    normalize_serving_phase3_contract(
        contract,
        route=route,
        project_dir=project_root,
        phase1_output=phase1,
    )
    return contract


def test_serving_phase3_contract_uses_wrapper_command_as_launch_contract(project_root: Path) -> None:
    phase1 = _phase1_surface("sglang_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "sglang serve --model-path tiny --port 8080"
    normalize_serving_phase1_surface(phase1)
    contract: dict[str, object] = {"launch_command": "sglang serve --model-path tiny --port 8080"}

    normalize_serving_phase3_contract(
        contract,
        route="sglang_serving",
        project_dir=project_root,
        phase1_output=phase1,
    )

    assert contract["project_dir"] == str(project_root)
    assert contract["entry_script_kind"] == "sglang_serving_validation"
    assert contract["migration_route"] == "sglang_serving"
    assert contract["service_launch_command"] == "sglang serve --model-path tiny --port 8080"
    assert str(contract["launch_command"]).endswith("validate_sglang_serving.py")
    assert contract["launch_command"] == contract["run_command"]
    assert validate_entry_script(contract)["passed"] is True


def test_generic_phase1_and_phase3_serving_are_not_ascend_forced(project_root: Path) -> None:
    phase1 = _phase1_surface()
    normalize_serving_phase1_surface(phase1)
    surface = _dict_value(phase1["serving_runtime_surface"])
    assert "npu_runtime_checks" not in surface
    assert "torch_npu" not in _string_list(surface.get("required_import_probes", []))

    contract = _normalized_contract(project_root)
    required_checks = _string_list(contract["required_checks"])
    assert "accelerator_execution_evidence" in required_checks
    assert "npu_execution_evidence" not in required_checks
    assert "npu_runtime_checks" not in contract
    assert validate_entry_script(contract)["passed"] is True


def test_generic_generated_serving_wrapper_has_no_npu_runtime_code(project_root: Path) -> None:
    contract = _normalized_contract(project_root)
    wrapper_path = Path(str(contract["entry_script_path"]))
    wrapper = wrapper_path.read_text(encoding="utf-8")

    assert "torch_npu" not in wrapper
    assert "npu_execution" not in wrapper
    assert "ASCEND_HOME_PATH" not in wrapper
    assert "CANN" not in wrapper
    assert "VLLM_TARGET_DEVICE" not in wrapper
    assert "CUDA_VISIBLE_DEVICES" not in wrapper
    assert "NCCL_" not in wrapper
    assert "torch.cuda.memory" not in wrapper
    assert 'cpu_fallback_detected = any("cpu" in marker for marker in forbidden_hits)' in wrapper
    assert '"accelerator_fallback_detected": accelerator_fallback_detected' in wrapper
    assert '"cpu_fallback_detected": cpu_fallback_detected' in wrapper


def test_serving_phase3_contract_replaces_stale_same_name_wrapper(project_root: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    normalize_serving_phase1_surface(phase1)
    stale_dir = project_root / "stale"
    stale_dir.mkdir()
    stale_wrapper = stale_dir / "validate_vllm_serving.py"
    _ = stale_wrapper.write_text("old wrapper", encoding="utf-8")
    contract: dict[str, object] = {
        "project_dir": str(project_root),
        "entry_script_path": str(stale_wrapper),
        "run_command": f"{stale_dir / '.venv' / 'bin' / 'python'} {stale_wrapper}",
    }

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=project_root,
        phase1_output=phase1,
    )

    generated_wrapper = project_root / "validate_vllm_serving.py"
    assert contract["entry_script_path"] == str(generated_wrapper)
    assert contract["run_command"] == f"{project_root / '.venv' / 'bin' / 'python'} {generated_wrapper}"
    assert "resolve_serving_endpoints" in generated_wrapper.read_text(encoding="utf-8")


def test_npu_ascend_generated_serving_wrapper_validates_live_openai_api(project_root: Path) -> None:
    contract = _normalized_contract(project_root)
    wrapper_path = Path(str(contract["entry_script_path"]))
    wrapper = wrapper_path.read_text(encoding="utf-8")

    assert "run_serving_validation" in wrapper
    assert "wait_for_health" in wrapper
    assert "resolve_served_model_id" in wrapper
    assert "resolve_serving_endpoints" in wrapper
    assert "validate_openai_api" in wrapper
    assert "openai_validation_payload" in wrapper
    assert "command_flag_value(command" in wrapper
    assert '"served_model_id": model_id' in wrapper
    assert "command_result = run_serving_validation(command, cwd=project_root, env=env)" in wrapper
    assert "http://127.0.0.1:19001" not in wrapper


def test_npu_ascend_generated_serving_wrapper_derives_urls_from_launch_command(project_root: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
    surface["readiness_probe"] = {"type": "http", "path": "/health"}
    surface["request_validation"] = {"type": "openai-compatible-http", "path": "/v1/chat/completions"}
    normalize_serving_phase1_surface(phase1)
    contract: dict[str, object] = {"project_dir": str(project_root)}

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=project_root,
        phase1_output=phase1,
    )

    wrapper_path = Path(str(contract["entry_script_path"]))
    namespace: dict[str, object] = {"__name__": "serving_wrapper_test"}
    exec(wrapper_path.read_text(encoding="utf-8"), namespace)
    resolver = cast(Callable[[list[str]], dict[str, str]], namespace["resolve_serving_endpoints"])
    endpoints = resolver(["vllm", "serve", "tiny", "--host", "0.0.0.0", "--port", "19001"])

    assert endpoints["health_url"] == "http://127.0.0.1:19001/health"
    assert endpoints["models_url"] == "http://127.0.0.1:19001/v1/models"
    assert endpoints["api_url"] == "http://127.0.0.1:19001/v1/chat/completions"


def test_serving_wrapper_normalizes_absolute_wildcard_probe_urls(project_root: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
    surface["readiness_probe"] = {"type": "http", "url": "http://0.0.0.0:19002/health"}
    surface["request_validation"] = {"type": "openai-compatible-http", "url": "http://0.0.0.0:19002/v1/completions"}
    normalize_serving_phase1_surface(phase1)
    contract: dict[str, object] = {"project_dir": str(project_root)}

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=project_root,
        phase1_output=phase1,
    )

    namespace: dict[str, object] = {"__name__": "serving_wrapper_test"}
    exec(Path(str(contract["entry_script_path"])).read_text(encoding="utf-8"), namespace)
    resolver = cast(Callable[[list[str]], dict[str, str]], namespace["resolve_serving_endpoints"])
    endpoints = resolver(["vllm", "serve", "tiny", "--host", "0.0.0.0", "--port", "19001"])

    assert endpoints["health_url"] == "http://127.0.0.1:19002/health"
    assert endpoints["models_url"] == "http://127.0.0.1:19002/v1/models"
    assert endpoints["api_url"] == "http://127.0.0.1:19002/v1/completions"


def test_all_vllm_wrappers_use_dynamic_openai_validation_without_project_hardcoding(project_root: Path) -> None:
    for policy_id in ("npu_ascend", "ppu_cuda_compatible", "musa_muxi", "generic_accelerator"):
        project_dir = project_root / policy_id
        project_dir.mkdir(parents=True, exist_ok=True)
        phase1 = _phase1_surface("vllm_serving")
        surface = _dict_value(phase1["serving_runtime_surface"])
        surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
        surface["readiness_probe"] = {"type": "http", "path": "/health"}
        surface["request_validation"] = {"type": "openai-compatible-http", "path": "/v1/chat/completions"}
        normalize_serving_phase1_surface(phase1)
        contract: dict[str, object] = {"project_dir": str(project_dir)}

        normalize_serving_phase3_contract(
            contract,
            route="vllm_serving",
            project_dir=project_dir,
            phase1_output=phase1,
        )

        wrapper = Path(str(contract["entry_script_path"])).read_text(encoding="utf-8")
        assert "http://127.0.0.1:19001" not in wrapper
        assert "resolve_serving_endpoints" in wrapper
        assert "resolve_served_model_id(models_url)" in wrapper
        assert "validate_openai_api(model_id, api_url)" in wrapper
        assert "openai_validation_payload" in wrapper
        assert "torch_npu" not in wrapper
        assert "VLLM_TARGET_DEVICE" not in wrapper
        assert "npu_execution" not in wrapper


def test_project_analysis_validator_accepts_generic_and_ascend_surfaces() -> None:
    generic = _phase1_surface()
    normalize_serving_phase1_surface(generic)
    ascend = _phase1_surface()
    normalize_serving_phase1_surface(ascend)

    assert validate_project_analysis(generic)["passed"] is True
    assert validate_project_analysis(ascend)["passed"] is True


def _final_gate_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "migration_route": "vllm_serving",
        "serving_framework": "vllm",
        "full_migration_status": "FULL_PASS",
        "project_test_files": ["tests/test_api.py"],
        "expected_outputs": ["200 OK"],
        "required_checks": [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "accelerator_execution_evidence",
            "no_forbidden_runtime_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
        ],
        "readiness_probe": {"passed": True},
        "request_validation": {"passed": True},
        "project_demo_or_test_executed": True,
        "serving_api_validated": True,
        "accelerator_execution_evidence": {"passed": True},
        "accelerator_execution_observed": True,
        "serving_runtime_evidence": {
            "vllm_imported": True,
            "forbidden_runtime_markers_absent": True,
        },
        "accelerator_fallback_detected": False,
        "cpu_fallback_detected": False,
        "import_only": False,
        "smoke_only": False,
    }
    return payload


def test_validation_final_gate_accepts_generic_evidence() -> None:
    assert validate_serving_final_gate(_final_gate_payload())["passed"] is True


def test_validation_final_gate_rejects_missing_execution_evidence() -> None:
    payload = _final_gate_payload()
    payload["required_checks"] = ["npu_execution_evidence"]
    _ = payload.pop("accelerator_execution_evidence")
    _ = payload.pop("accelerator_execution_observed")

    result = validate_serving_final_gate(payload)

    assert result["passed"] is False
    assert any("accelerator_execution_evidence" in error for error in result["errors"])


def test_validation_final_gate_rejects_missing_runtime_evidence() -> None:
    payload = _final_gate_payload()
    _ = payload.pop("serving_runtime_evidence")

    result = validate_serving_final_gate(payload)

    assert result["passed"] is False
    assert any("serving_runtime_evidence" in error for error in result["errors"])
