from pathlib import Path
import os
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


def test_operator_launcher_uses_v3_default_port_and_bypasses_loopback_proxy(
    tmp_path: Path,
) -> None:
    task = tmp_path / "level1" / "Abs"
    task.mkdir(parents=True)
    (task / "definition.json").write_text("{}", encoding="utf-8")
    (task / "workload.jsonl").write_text("{}\n", encoding="utf-8")
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "pyproject.toml").write_text("[project]\nname='bench'\n", encoding="utf-8")
    capture = tmp_path / "captured.txt"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf 'NO_PROXY=%s\\nno_proxy=%s\\n' \"$NO_PROXY\" \"$no_proxy\" > \"$CAPTURE\"\n"
        "printf 'ARG=%s\\n' \"$@\" >> \"$CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "CAPTURE": str(capture),
            "NO_PROXY": "existing-no-proxy",
            "no_proxy": "existing-lower-proxy",
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            str(task),
            str(bench),
            "--target-soc",
            "Ascend910B3",
            "--operator-output-dir",
            str(tmp_path / "operator-output"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    captured = capture.read_text(encoding="utf-8")
    assert "existing-no-proxy" in captured
    assert "existing-lower-proxy" in captured
    assert "127.0.0.1" in captured
    assert "localhost" in captured
    assert "::1" in captured
    assert "http://127.0.0.1:4096" in captured
