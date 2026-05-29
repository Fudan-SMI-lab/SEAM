from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"


def test_baseaware_phase2_prompts_forbid_blocking_questions() -> None:
    for prompt_name in (
        "phase_2_venv_create_ppu_container_baseaware",
        "phase_2_venv_create_musa_container_baseaware",
    ):
        content = (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8")
        lowered = content.lower()
        assert "do not ask the user" in lowered
        assert "question" in lowered
        assert "safest autonomous option" in lowered


def test_phase1_platform_prompts_include_serving_route_taxonomy_without_npu_copying() -> None:
    expectations = {
        "phase_1_project_analysis_ppu": "serving_backend` to `ppu`",
        "phase_1_project_analysis_musa": "serving_backend` to `musa`",
    }
    for prompt_name, backend_text in expectations.items():
        content = (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8")
        assert "vllm_serving" in content
        assert "sglang_serving" in content
        assert "ordinary_cuda" in content
        assert "serving_runtime_surface" in content
        assert backend_text in content
        assert "do not copy Ascend/NPU-only requirements" in content
