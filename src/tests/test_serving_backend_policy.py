from collections.abc import Callable
from pathlib import Path
from typing import cast

from core.platform_policy import BUILTIN_PRESETS, TargetPlatformConfig, resolve_policy
from core.routes import normalize_serving_phase1_surface, normalize_serving_phase3_contract
from validators.validate_entry_script import validate as validate_entry_script
from validators.validate_project_analysis import validate as validate_project_analysis
from validators.validate_validation_final import validate_serving_final_gate


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


def _normalized_contract(tmp_path: Path, policy_id: str, route: str = "vllm_serving") -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    policy = BUILTIN_PRESETS[policy_id]
    phase1 = _phase1_surface(route)
    normalize_serving_phase1_surface(phase1, platform_policy=policy)
    contract: dict[str, object] = {"project_dir": str(tmp_path)}
    normalize_serving_phase3_contract(
        contract,
        route=route,
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=policy,
    )
    return contract


def test_serving_phase3_contract_uses_wrapper_command_as_launch_contract(tmp_path: Path) -> None:
    phase1 = _phase1_surface("sglang_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "sglang serve --model-path tiny --port 8080"
    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["generic_accelerator"])
    contract: dict[str, object] = {"launch_command": "sglang serve --model-path tiny --port 8080"}

    normalize_serving_phase3_contract(
        contract,
        route="sglang_serving",
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=BUILTIN_PRESETS["generic_accelerator"],
    )

    assert contract["project_dir"] == str(tmp_path)
    assert contract["entry_script_kind"] == "sglang_serving_validation"
    assert contract["migration_route"] == "sglang_serving"
    assert contract["service_launch_command"] == "sglang serve --model-path tiny --port 8080"
    assert str(contract["launch_command"]).endswith("validate_sglang_serving.py")
    assert contract["launch_command"] == contract["run_command"]
    assert validate_entry_script(contract)["passed"] is True


def test_platform_policy_serving_backend_presets() -> None:
    assert BUILTIN_PRESETS["npu_ascend"].serving_runtime.backend == "ascend"
    assert BUILTIN_PRESETS["ppu_cuda_compatible"].serving_runtime.backend == "ppu"
    assert BUILTIN_PRESETS["musa_muxi"].serving_runtime.backend == "musa"
    assert BUILTIN_PRESETS["generic_accelerator"].serving_runtime.backend == "generic"


def test_platform_policy_serving_backend_override() -> None:
    policy = resolve_policy(
        TargetPlatformConfig(
            preset="generic_accelerator",
            overrides={"serving_runtime": {"backend": "custom_backend"}},
        ),
        "workflow",
    )

    assert policy.serving_runtime.backend == "custom_backend"


def test_phase1_serving_backend_framework_name_uses_platform_backend() -> None:
    phase1 = _phase1_surface()
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["serving_backend"] = "vllm"

    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["npu_ascend"])

    normalized = _dict_value(phase1["serving_runtime_surface"])
    assert normalized["serving_framework"] == "vllm"
    assert normalized["serving_backend"] == "ascend"


def test_generic_phase1_and_phase3_serving_are_not_ascend_forced(tmp_path: Path) -> None:
    phase1 = _phase1_surface()
    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["generic_accelerator"])
    surface = _dict_value(phase1["serving_runtime_surface"])
    assert surface["serving_backend"] == "generic"
    assert "ascend_runtime_checks" not in surface
    assert "torch_npu" not in _string_list(surface.get("required_import_probes", []))

    contract = _normalized_contract(tmp_path, "generic_accelerator")
    required_checks = _string_list(contract["required_checks"])
    assert contract["serving_backend"] == "generic"
    assert "accelerator_execution_evidence" in required_checks
    assert "npu_execution_evidence" not in required_checks
    assert "ascend_runtime_checks" not in contract
    assert validate_entry_script(contract)["passed"] is True


def test_generic_generated_serving_wrapper_has_no_npu_runtime_code(tmp_path: Path) -> None:
    contract = _normalized_contract(tmp_path, "generic_accelerator")
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
    assert '"cuda_fallback_detected": cuda_fallback_detected' in wrapper
    assert '"cpu_fallback_detected": cpu_fallback_detected' in wrapper


def test_ppu_and_muxi_phase3_contracts_use_platform_backend(tmp_path: Path) -> None:
    ppu_contract = _normalized_contract(tmp_path / "ppu", "ppu_cuda_compatible")
    muxi_contract = _normalized_contract(tmp_path / "muxi", "musa_muxi", route="sglang_serving")

    assert ppu_contract["serving_backend"] == "ppu"
    assert muxi_contract["serving_backend"] == "musa"
    for contract in (ppu_contract, muxi_contract):
        required_checks = _string_list(contract["required_checks"])
        forbidden_markers = _string_list(contract["forbidden_runtime_markers"])
        assert "accelerator_execution_evidence" in required_checks
        assert "npu_execution_evidence" not in required_checks
        assert "no_forbidden_runtime_fallback" in required_checks
        assert "no_cuda_fallback" not in required_checks
        assert "ascend_runtime_checks" not in contract
        assert "CUDA_VISIBLE_DEVICES" not in forbidden_markers
        assert "NCCL_" not in forbidden_markers
        assert "torch.cuda.memory" not in forbidden_markers
        assert validate_entry_script(contract)["passed"] is True


def test_explicit_phase1_backend_wins_over_policy(tmp_path: Path) -> None:
    phase1 = _phase1_surface()
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["serving_backend"] = "explicit_backend"

    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["npu_ascend"])
    contract: dict[str, object] = {"project_dir": str(tmp_path)}
    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=BUILTIN_PRESETS["npu_ascend"],
    )

    assert contract["serving_backend"] == "explicit_backend"
    required_checks = _string_list(contract["required_checks"])
    assert "accelerator_execution_evidence" in required_checks
    assert "npu_execution_evidence" not in required_checks


def test_npu_ascend_policy_keeps_ascend_contract(tmp_path: Path) -> None:
    contract = _normalized_contract(tmp_path, "npu_ascend")
    required_checks = _string_list(contract["required_checks"])
    required_import_probes = _string_list(contract["required_import_probes"])

    assert contract["serving_backend"] == "ascend"
    assert "npu_execution_evidence" in required_checks
    assert "ascend_runtime_checks" in contract
    assert "torch_npu" in required_import_probes
    assert validate_entry_script(contract)["passed"] is True


def test_npu_ascend_generated_serving_wrapper_keeps_ascend_runtime_code(tmp_path: Path) -> None:
    contract = _normalized_contract(tmp_path, "npu_ascend")
    wrapper_path = Path(str(contract["entry_script_path"]))
    wrapper = wrapper_path.read_text(encoding="utf-8")

    assert "torch_npu" in wrapper
    assert "npu_execution_evidence" in wrapper
    assert "ASCEND_HOME_PATH" in wrapper
    assert 'env.setdefault("VLLM_TARGET_DEVICE", "npu")' in wrapper


def test_serving_phase3_contract_replaces_stale_same_name_wrapper(tmp_path: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["npu_ascend"])
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    stale_wrapper = stale_dir / "validate_vllm_serving.py"
    _ = stale_wrapper.write_text("old wrapper", encoding="utf-8")
    contract: dict[str, object] = {
        "project_dir": str(tmp_path),
        "entry_script_path": str(stale_wrapper),
        "run_command": f"{stale_dir / '.venv' / 'bin' / 'python'} {stale_wrapper}",
    }

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=BUILTIN_PRESETS["npu_ascend"],
    )

    generated_wrapper = tmp_path / "validate_vllm_serving.py"
    assert contract["entry_script_path"] == str(generated_wrapper)
    assert contract["run_command"] == f"{tmp_path / '.venv' / 'bin' / 'python'} {generated_wrapper}"
    assert "resolve_serving_endpoints" in generated_wrapper.read_text(encoding="utf-8")


def test_npu_ascend_generated_serving_wrapper_validates_live_openai_api(tmp_path: Path) -> None:
    contract = _normalized_contract(tmp_path, "npu_ascend")
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


def test_npu_ascend_generated_serving_wrapper_derives_urls_from_launch_command(tmp_path: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
    surface["readiness_probe"] = {"type": "http", "path": "/health"}
    surface["request_validation"] = {"type": "openai-compatible-http", "path": "/v1/chat/completions"}
    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["npu_ascend"])
    contract: dict[str, object] = {"project_dir": str(tmp_path)}

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=BUILTIN_PRESETS["npu_ascend"],
    )

    wrapper_path = Path(str(contract["entry_script_path"]))
    namespace: dict[str, object] = {"__name__": "serving_wrapper_test"}
    exec(wrapper_path.read_text(encoding="utf-8"), namespace)
    resolver = cast(Callable[[list[str]], dict[str, str]], namespace["resolve_serving_endpoints"])
    endpoints = resolver(["vllm", "serve", "tiny", "--host", "0.0.0.0", "--port", "19001"])

    assert endpoints["health_url"] == "http://127.0.0.1:19001/health"
    assert endpoints["models_url"] == "http://127.0.0.1:19001/v1/models"
    assert endpoints["api_url"] == "http://127.0.0.1:19001/v1/chat/completions"


def test_serving_wrapper_normalizes_absolute_wildcard_probe_urls(tmp_path: Path) -> None:
    phase1 = _phase1_surface("vllm_serving")
    surface = _dict_value(phase1["serving_runtime_surface"])
    surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
    surface["readiness_probe"] = {"type": "http", "url": "http://0.0.0.0:19002/health"}
    surface["request_validation"] = {"type": "openai-compatible-http", "url": "http://0.0.0.0:19002/v1/completions"}
    normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS["generic_accelerator"])
    contract: dict[str, object] = {"project_dir": str(tmp_path)}

    normalize_serving_phase3_contract(
        contract,
        route="vllm_serving",
        project_dir=tmp_path,
        phase1_output=phase1,
        platform_policy=BUILTIN_PRESETS["generic_accelerator"],
    )

    namespace: dict[str, object] = {"__name__": "serving_wrapper_test"}
    exec(Path(str(contract["entry_script_path"])).read_text(encoding="utf-8"), namespace)
    resolver = cast(Callable[[list[str]], dict[str, str]], namespace["resolve_serving_endpoints"])
    endpoints = resolver(["vllm", "serve", "tiny", "--host", "0.0.0.0", "--port", "19001"])

    assert endpoints["health_url"] == "http://127.0.0.1:19002/health"
    assert endpoints["models_url"] == "http://127.0.0.1:19002/v1/models"
    assert endpoints["api_url"] == "http://127.0.0.1:19002/v1/completions"


def test_all_vllm_wrappers_use_dynamic_openai_validation_without_project_hardcoding(tmp_path: Path) -> None:
    for policy_id in ("npu_ascend", "ppu_cuda_compatible", "musa_muxi", "generic_accelerator"):
        project_dir = tmp_path / policy_id
        project_dir.mkdir(parents=True, exist_ok=True)
        phase1 = _phase1_surface("vllm_serving")
        surface = _dict_value(phase1["serving_runtime_surface"])
        surface["launch_command"] = "vllm serve tiny --host 0.0.0.0 --port 19001"
        surface["readiness_probe"] = {"type": "http", "path": "/health"}
        surface["request_validation"] = {"type": "openai-compatible-http", "path": "/v1/chat/completions"}
        normalize_serving_phase1_surface(phase1, platform_policy=BUILTIN_PRESETS[policy_id])
        contract: dict[str, object] = {"project_dir": str(project_dir)}

        normalize_serving_phase3_contract(
            contract,
            route="vllm_serving",
            project_dir=project_dir,
            phase1_output=phase1,
            platform_policy=BUILTIN_PRESETS[policy_id],
        )

        wrapper = Path(str(contract["entry_script_path"])).read_text(encoding="utf-8")
        assert "http://127.0.0.1:19001" not in wrapper
        assert "resolve_serving_endpoints" in wrapper
        assert "resolve_served_model_id(models_url)" in wrapper
        assert "validate_openai_api(model_id, api_url)" in wrapper
        assert "openai_validation_payload" in wrapper
        if policy_id == "npu_ascend":
            assert "torch_npu" in wrapper
            assert 'env.setdefault("VLLM_TARGET_DEVICE", "npu")' in wrapper
        else:
            assert "torch_npu" not in wrapper
            assert "VLLM_TARGET_DEVICE" not in wrapper
            assert "npu_execution" not in wrapper


def test_project_analysis_validator_accepts_generic_and_ascend_surfaces() -> None:
    generic = _phase1_surface()
    normalize_serving_phase1_surface(generic, platform_policy=BUILTIN_PRESETS["generic_accelerator"])
    ascend = _phase1_surface()
    normalize_serving_phase1_surface(ascend, platform_policy=BUILTIN_PRESETS["npu_ascend"])

    assert validate_project_analysis(generic)["passed"] is True
    assert validate_project_analysis(ascend)["passed"] is True


def test_project_analysis_validator_rejects_missing_generic_backend() -> None:
    payload = _phase1_surface()
    normalize_serving_phase1_surface(payload, platform_policy=BUILTIN_PRESETS["generic_accelerator"])
    surface = _dict_value(payload["serving_runtime_surface"])
    surface["serving_backend"] = ""

    result = validate_project_analysis(payload)

    assert result["passed"] is False
    assert any("serving_backend" in error for error in result["errors"])


def _final_gate_payload(backend: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "migration_route": "vllm_serving",
        "serving_framework": "vllm",
        "serving_backend": backend,
        "full_migration_status": "FULL_PASS",
        "project_test_files": ["tests/test_api.py"],
        "expected_outputs": ["200 OK"],
        "required_checks": [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "no_forbidden_runtime_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
        ],
        "readiness_probe": {"passed": True},
        "request_validation": {"passed": True},
        "project_demo_or_test_executed": True,
        "serving_api_validated": True,
        "cuda_fallback_detected": False,
        "cpu_fallback_detected": False,
        "import_only": False,
        "smoke_only": False,
    }
    if backend == "ascend":
        payload["required_checks"] = [
            "project_demo_or_test_execution",
            "serving_api_request_validation",
            "readiness_probe_passed",
            "no_cuda_fallback",
            "no_cpu_fallback",
            "fresh_serving_report",
            "route_framework_match",
            "npu_execution_evidence",
        ]
        payload["npu_execution_evidence"] = {"passed": True}
        payload["npu_execution_observed"] = True
        payload["ascend_runtime_evidence"] = {
            "serving_backend": "ascend",
            "cann_env_loaded": True,
            "torch_npu_imported": True,
            "tbe_imported": True,
            "te_imported": True,
            "vllm_imported": True,
            "forbidden_runtime_markers_absent": True,
        }
    else:
        payload["required_checks"] = [*_string_list(payload["required_checks"]), "accelerator_execution_evidence"]
        payload["accelerator_execution_evidence"] = {"passed": True}
        payload["accelerator_execution_observed"] = True
        payload["serving_runtime_evidence"] = {
            "serving_backend": backend,
            "vllm_imported": True,
            "forbidden_runtime_markers_absent": True,
        }
    return payload


def test_validation_final_gate_accepts_generic_and_ascend_evidence() -> None:
    assert validate_serving_final_gate(_final_gate_payload("generic"))["passed"] is True
    assert validate_serving_final_gate(_final_gate_payload("ascend"))["passed"] is True


def test_validation_final_gate_rejects_generic_npu_only_evidence() -> None:
    payload = _final_gate_payload("generic")
    payload["required_checks"] = ["npu_execution_evidence"]
    _ = payload.pop("accelerator_execution_evidence")
    _ = payload.pop("accelerator_execution_observed")

    result = validate_serving_final_gate(payload)

    assert result["passed"] is False
    assert any("accelerator_execution_evidence" in error for error in result["errors"])


def test_validation_final_gate_keeps_strict_ascend_evidence() -> None:
    payload = _final_gate_payload("ascend")
    _ = payload.pop("ascend_runtime_evidence")

    result = validate_serving_final_gate(payload)

    assert result["passed"] is False
    assert any("ascend_runtime_evidence" in error for error in result["errors"])
