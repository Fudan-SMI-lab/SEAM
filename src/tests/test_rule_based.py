import os
import sys
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from migrator.rule_based import RuleBasedMigrator


@pytest.fixture
def migrator():
    return RuleBasedMigrator(strategy="cuda_to_npu")


def test_default_rule_based_migrator_is_report_only():
    code = 'import torch\nx = torch.cuda.is_available()\ny = x.cuda()\nz = ("cuda")'
    result, report = RuleBasedMigrator().migrate(code)
    assert result == code
    assert "import torch_npu" not in result
    assert report["mode"] == "report_only"
    assert report["total_replacements"] == 0
    assert report["rules"]["torch_cuda_references"] == 1
    assert report["rules"]["cuda_method_calls"] == 1
    assert report["rules"]["inject_torch_npu"] == 0


def test_target_platform_npu_enables_legacy_rewrite():
    code = 'import torch\nx = torch.cuda.is_available()\ny = x.cuda()\nz = ("cuda")'
    result, report = RuleBasedMigrator(target_platform="npu").migrate(code)
    assert "torch.npu.is_available()" in result
    assert ".npu()" in result
    assert '"npu"' in result
    assert "import torch_npu" in result
    assert report["mode"] == "rewrite"
    assert report["total_replacements"] >= 4


class TestMigrate:
    def test_torch_cuda_to_npu(self, migrator):
        code = "import torch\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert "torch.npu.is_available()" in result
        assert "import torch_npu" in result

    def test_cuda_method_to_npu(self, migrator):
        code = "model.cuda()\ntensor.cuda(device=0)"
        result, report = migrator.migrate(code)
        assert "model.npu()" in result
        assert "tensor.npu(device=0)" in result
        assert report["rules"]["cuda_method"] == 2

    def test_cuda_string_literal(self, migrator):
        code = 'x.to("cuda")\ny.to("cuda", non_blocking=True)'
        result, report = migrator.migrate(code)
        assert 'x.to("npu")' in result
        assert 'y.to("npu", non_blocking=True)' in result

    def test_cuda_string_literal_single_quotes(self, migrator):
        code = "x.to('cuda')"
        result, report = migrator.migrate(code)
        assert "x.to('npu')" in result

    def test_nccl_to_hccl_double_quotes(self, migrator):
        code = 'dist.init_process_group("nccl")'
        result, report = migrator.migrate(code)
        assert '"hccl"' in result

    def test_nccl_to_hccl_single_quotes(self, migrator):
        code = "dist.init_process_group('nccl')"
        result, report = migrator.migrate(code)
        assert "'hccl'" in result

    def test_torch_cuda_amp(self, migrator):
        code = "with torch.cuda.amp.autocast(): pass"
        result, report = migrator.migrate(code)
        assert "torch.npu.amp.autocast()" in result
        assert report["rules"]["torch_cuda_amp"] == 1

    def test_torch_npu_not_injected_for_no_cuda(self, migrator):
        code = "import torch\nx = torch.randn(3, 3)"
        result, report = migrator.migrate(code)
        assert "import torch_npu" not in result
        assert report["rules"]["inject_torch_npu"] == 0

    def test_no_changes_for_non_cuda_code(self, migrator):
        code = "import torch\nx = torch.randn(3)\ny = x + 1"
        result, report = migrator.migrate(code)
        assert result == code
        assert report["total_replacements"] == 0

    def test_no_double_injection(self, migrator):
        code = "import torch\nimport torch_npu\nx = torch.cuda.is_available()"
        result, report = migrator.migrate(code)
        assert result.count("import torch_npu") == 1

    def test_report_has_per_rule_counts(self, migrator):
        code = 'model.cuda()\ndist.init_process_group("nccl")\nwith torch.cuda.amp.autocast(): pass'
        _, report = migrator.migrate(code)
        assert "rules" in report
        assert "inject_torch_npu" in report["rules"]
        assert "cuda_method" in report["rules"]
        assert "nccl_string_literal_double" in report["rules"]
        assert "torch_cuda_amp" in report["rules"]

    def test_total_replacements_count(self, migrator):
        code = "a.cuda()\nb.cuda()\nc.cuda()"
        _, report = migrator.migrate(code)
        assert report["total_replacements"] == 4

    def test_inject_after_imports(self, migrator):
        code = "import os\nimport torch\nfrom pathlib import Path\n\nx = torch.cuda.current_device()"
        result, _ = migrator.migrate(code)
        lines = result.split("\n")
        assert "import torch_npu" in lines[3]


class TestMigrateFile:
    def test_migrate_file(self, migrator, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("import torch\nx = torch.cuda.is_available()")
        result, report = migrator.migrate_file(str(test_file))
        assert "torch.npu.is_available()" in result
        assert "import torch_npu" in result

    def test_migrate_file_not_found(self, migrator):
        with pytest.raises(FileNotFoundError):
            migrator.migrate_file("/nonexistent/file.py")


class TestMigrateDirectory:
    def test_migrate_directory(self, migrator, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("import torch\nx = torch.cuda.is_available()")
        f2 = tmp_path / "b.py"
        f2.write_text("import torch\ny = torch.randn(3)")
        sub = tmp_path / "sub"
        sub.mkdir()
        f3 = sub / "c.py"
        f3.write_text("import torch\nz = torch.cuda.manual_seed(42)")
        report = migrator.migrate_directory(str(tmp_path))
        assert report["summary"]["total_files"] == 3
        assert report["summary"]["total_replacements"] >= 3
        assert str(f1) in report["files"]
        assert str(f3) in report["files"]

    def test_migrate_directory_pattern(self, migrator, tmp_path):
        py_file = tmp_path / "script.py"
        py_file.write_text("import torch\ntorch.cuda.empty_cache()")
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("use torch.cuda for GPU")
        report = migrator.migrate_directory(str(tmp_path), pattern="*.py")
        assert report["summary"]["total_files"] == 1
        assert str(py_file) in report["files"]
        assert str(txt_file) not in report["files"]
