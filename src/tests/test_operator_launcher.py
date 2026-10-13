from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "src" / "scripts" / "run_operator.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_operator_launcher_help_documents_usage() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "OPERATOR_TASK_DIR" in result.stdout
    assert "--target-soc" in result.stdout


def test_operator_launcher_requires_task_and_kernelbench() -> None:
    result = _run()
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_operator_launcher_rejects_missing_task_files(tmp_path: Path) -> None:
    task = tmp_path / "GELU"
    task.mkdir()
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "pyproject.toml").write_text("[project]\nname='bench'\n", encoding="utf-8")
    result = _run(str(task), str(bench), "--target-soc", "Ascend910B3")
    assert result.returncode == 2
    assert "definition.json" in result.stderr
