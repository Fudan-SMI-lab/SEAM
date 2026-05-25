import json
import os

from core.experience_query import ExperienceQuerier
from core.experience_store import ExperienceStore


def read_raw_legacy_index(store: ExperienceStore) -> list[dict[str, object]]:
    if not os.path.isfile(store.index_path):
        return []
    with open(store.index_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_manifest_created_from_catalog_upsert(tmp_path):
    store = ExperienceStore(str(tmp_path))

    store.upsert_catalog_entry({
        "id": "promoted-npu-fix",
        "type": "skill",
        "status": "promoted",
        "title": "NPU Fix",
        "category": "operator_incompat",
        "subtype": "flash_attention",
        "tags": ["torch-npu"],
        "confidence": 0.9,
    })

    with open(store.manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["schema_version"] == 1
    assert manifest["counts"]["total"] == 1
    assert manifest["counts"]["by_type"] == {"skill": 1}
    assert manifest["counts"]["by_status"] == {"promoted": 1}
    assert manifest["storage_roots"]["catalog"] == os.path.join("memory", "index", "experiences.jsonl")
    assert manifest["storage_roots"]["local_skills"] == ".skills"


def test_catalog_upsert_read_preserves_required_fields(tmp_path):
    store = ExperienceStore(str(tmp_path))

    store.upsert_catalog_entry({
        "id": "promoted-npu-fix",
        "type": "skill",
        "status": "promoted",
        "title": "NPU Fix",
        "category": "dependency",
        "subtype": "torch_npu_install",
        "tags": ["torch-npu", "pip"],
        "confidence": 0.8,
        "target_roles": ["dependency_fixer"],
        "target_phases": ["phase_5_validation"],
        "trigger_fingerprint": "dependency|torch_npu_install|pip|torch-npu",
        "asset_paths": [".memory/skills/npu-fix/skill_data.json"],
        "source_runs": ["run-1"],
    })

    entry = store.read_catalog()[0]

    for field in [
        "id", "type", "status", "title", "category", "subtype", "tags", "confidence",
        "target_roles", "target_phases", "trigger_fingerprint", "asset_paths", "source_runs",
        "created_at", "updated_at", "last_used_at", "use_count", "failure_count", "usage",
    ]:
        assert field in entry
    assert entry["target_roles"] == ["dependency_fixer"]
    assert entry["use_count"] == 0
    assert entry["usage"]["selected_count"] == 0


def test_record_experience_usage_updates_catalog_and_legacy_metadata(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({"id": "legacy-exp", "status": "promoted", "type": "skill"})
    store.upsert_catalog_entry({"id": "legacy-exp", "status": "promoted", "type": "skill"})

    store.record_experience_usage(
        selected_ids=["legacy-exp"],
        used_ids=["legacy-exp"],
        ignored_ids=[],
        verification={"experience_ids": ["legacy-exp"], "passed": True},
    )

    catalog_entry = store.read_catalog()[0]
    legacy_entry = store.read_index()[0]
    assert catalog_entry["id"] == "legacy-exp"
    assert catalog_entry["type"] == "skill"
    assert catalog_entry["status"] == "promoted"
    assert catalog_entry["usage"]["selected_count"] == 1
    assert catalog_entry["usage"]["used_count"] == 1
    assert catalog_entry["usage"]["verification_success_count"] == 1
    assert catalog_entry["use_count"] == 1
    assert legacy_entry["id"] == "legacy-exp"
    assert legacy_entry["type"] == "skill"
    assert legacy_entry["status"] == "promoted"
    assert legacy_entry["usage"]["selected_count"] == 1


def test_catalog_upsert_preserves_existing_usage_counters(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_catalog_entry({"id": "preserve-exp", "status": "promoted", "type": "skill"})
    store.record_experience_usage(selected_ids=["preserve-exp"], used_ids=["preserve-exp"])

    store.upsert_catalog_entry({"id": "preserve-exp", "status": "promoted", "type": "skill", "title": "Updated"})

    entry = store.read_catalog()[0]
    assert entry["title"] == "Updated"
    assert entry["usage"]["selected_count"] == 1
    assert entry["usage"]["used_count"] == 1
    assert entry["use_count"] == 1


def test_rebuild_catalog_from_skill_data(tmp_path):
    store = ExperienceStore(str(tmp_path))
    skill_dir = tmp_path / ".memory" / "skills" / "npu-flash-attn"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill_data.json").write_text(json.dumps({
        "name": "npu-flash-attn",
        "title": "Flash Attention NPU Fix",
        "category": "operator_incompat",
        "subtype": "flash_attention",
        "tags": ["torch-npu", "flash-attn"],
        "confidence": 0.95,
        "promotion_type": "skill",
        "merged_from_runs": ["run-1", "run-2"],
    }), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# Flash Attention NPU Fix\n", encoding="utf-8")

    entries = store.rebuild_catalog()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["id"] == "promoted-npu-flash-attn"
    assert entry["source_runs"] == ["run-1", "run-2"]
    assert os.path.join(".memory", "skills", "npu-flash-attn", "skill_data.json") in entry["asset_paths"]


def test_rebuild_catalog_discovers_local_skill_pack_assets(tmp_path):
    store = ExperienceStore(str(tmp_path))
    skill_dir = tmp_path / ".skills" / "cuda-custom-op-to-npu-custom-op"
    nested_dir = skill_dir / "templates"
    nested_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# CUDA Custom Op\n\nLocal pack guidance\n", encoding="utf-8")
    (nested_dir / "example.txt").write_text("template", encoding="utf-8")
    cache_dir = skill_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "ignored.pyc").write_text("cache", encoding="utf-8")

    entries = store.rebuild_catalog()
    by_id = {entry["id"]: entry for entry in entries}
    entry = by_id["local-skill-cuda-custom-op-to-npu-custom-op"]

    assert entry["type"] == "skill-pack"
    assert entry["status"] == "local"
    assert entry["category"] == "local_skill_pack"
    assert entry["title"] == "CUDA Custom Op"
    assert entry["asset_paths"][0] == os.path.join(".skills", "cuda-custom-op-to-npu-custom-op", "SKILL.md")
    assert os.path.join(".skills", "cuda-custom-op-to-npu-custom-op", "templates", "example.txt") in entry["asset_paths"]
    assert all("__pycache__" not in path for path in entry["asset_paths"])
    assert store.validate_integrity()["ok"] is True

    with open(store.manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["storage_roots"]["local_skills"] == ".skills"


def test_read_index_exposes_local_skill_pack_without_replacing_legacy_index(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({"id": "legacy-exp", "type": "document", "status": "promoted"})
    skill_dir = tmp_path / ".skills" / "local-pack"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Local Pack\n", encoding="utf-8")
    store.rebuild_catalog()

    index_ids = {entry["id"] for entry in store.read_index()}

    assert index_ids == {"legacy-exp", "local-skill-local-pack"}
    assert [entry["id"] for entry in read_raw_legacy_index(store)] == ["legacy-exp"]


def test_upsert_index_does_not_persist_synthesized_local_skill_pack(tmp_path):
    store = ExperienceStore(str(tmp_path))
    skill_dir = tmp_path / ".skills" / "local-pack"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Local Pack\n", encoding="utf-8")
    store.rebuild_catalog()

    store.upsert_index({"id": "legacy-exp", "type": "document", "status": "promoted"})

    assert [entry["id"] for entry in read_raw_legacy_index(store)] == ["legacy-exp"]
    assert {entry["id"] for entry in store.read_index()} == {"legacy-exp", "local-skill-local-pack"}


def test_record_usage_for_local_skill_pack_does_not_create_legacy_index(tmp_path):
    store = ExperienceStore(str(tmp_path))
    skill_dir = tmp_path / ".skills" / "local-pack"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Local Pack\n", encoding="utf-8")
    store.rebuild_catalog()

    store.record_experience_usage(selected_ids=["local-skill-local-pack"])

    assert not os.path.exists(store.index_path)
    catalog_entry = {entry["id"]: entry for entry in store.read_catalog()}["local-skill-local-pack"]
    assert catalog_entry["usage"]["selected_count"] == 1
    assert [entry["id"] for entry in store.read_index()] == ["local-skill-local-pack"]


def test_experience_query_loads_local_skill_pack_from_asset_paths(tmp_path):
    store = ExperienceStore(str(tmp_path))
    skill_dir = tmp_path / ".skills" / "local-pack"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Local Pack\n\nUse local guidance.\n", encoding="utf-8")
    store.rebuild_catalog()

    class SelectorSession:
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "local-skill-local-pack" in command
            assert os.path.join(".skills", "local-pack", "SKILL.md") in command
            return json.dumps({
                "selected_experiences": [{
                    "id": "local-skill-local-pack",
                    "type": "skill-pack",
                    "relevance_score": 0.9,
                    "reasoning": "local skill pack match",
                }],
                "summary": "selected local pack",
                "warning": "",
            })

    result = ExperienceQuerier(store, SelectorSession()).query({})

    selected = result["selected_experiences"]
    assert [exp["id"] for exp in selected] == ["local-skill-local-pack"]
    assert selected[0]["type"] == "skill-pack"
    assert selected[0]["body"].startswith("# Local Pack")
    assert selected[0]["file_path"].endswith(os.path.join(".skills", "local-pack", "SKILL.md"))


def test_experience_query_skips_inactive_statuses(tmp_path):
    store = ExperienceStore(str(tmp_path))
    active_path = tmp_path / "active.json"
    active_path.write_text(json.dumps({"title": "Active Fix"}), encoding="utf-8")
    store.upsert_index({
        "id": "active-exp",
        "type": "document",
        "status": "promoted",
        "title": "Active Fix",
        "asset_paths": [str(active_path)],
    })

    for status in ("rejected", "quarantined", "archived", "consumed"):
        path = tmp_path / f"{status}.json"
        path.write_text(json.dumps({"title": status}), encoding="utf-8")
        store.upsert_index({
            "id": f"{status}-exp",
            "type": "document",
            "status": status,
            "title": status,
            "asset_paths": [str(path)],
        })

    class SelectorSession:
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "active-exp" in command
            assert "rejected-exp" not in command
            assert "quarantined-exp" not in command
            assert "archived-exp" not in command
            assert "consumed-exp" not in command
            return json.dumps({
                "selected_experiences": [{"id": "active-exp", "type": "document"}],
                "summary": "active only",
                "warning": "",
            })

    result = ExperienceQuerier(store, SelectorSession()).query({})

    assert [exp["id"] for exp in result["selected_experiences"]] == ["active-exp"]


def test_experience_query_filters_aten_only_custom_op_under_native_gate(tmp_path):
    store = ExperienceStore(str(tmp_path))
    stale_path = tmp_path / "aten.json"
    stale_path.write_text(json.dumps({"title": "ATen-only"}), encoding="utf-8")
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps({"title": "AscendC native"}), encoding="utf-8")
    store.upsert_index({
        "id": "aten-exp",
        "type": "document",
        "status": "staging",
        "title": "Port CUDA custom extension to NPU-routed C++/ATen extension",
        "subtype": "cuda_extension_to_npu_routed_cpp_extension",
        "tags": ["custom-op", "aten", "cpp-extension"],
        "asset_paths": [str(stale_path)],
    })
    store.upsert_index({
        "id": "native-exp",
        "type": "document",
        "status": "local",
        "title": "Build real AscendC CANN OPP custom op artifacts",
        "subtype": "ascendc_opp_custom_op",
        "tags": ["custom-op", "ascendc", "cann", "opp"],
        "asset_paths": [str(native_path)],
    })

    class SelectorSession:
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "native-exp" in command
            assert "aten-exp" not in command
            return json.dumps({
                "selected_experiences": [{"id": "native-exp", "type": "document"}],
                "summary": "native only",
                "warning": "",
            })

    result = ExperienceQuerier(store, SelectorSession()).query({
        "phase": "analyze_error",
        "parent_phase": "phase_5_validation",
        "roles": ["operator_fixer"],
        "custom_op_native_gate_required": "true",
    })

    assert [exp["id"] for exp in result["selected_experiences"]] == ["native-exp"]


def test_experience_query_filters_loaded_aten_body_under_native_gate(tmp_path):
    store = ExperienceStore(str(tmp_path))
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps({
        "title": "Require fail-closed per-unit custom-op final-gate evidence",
        "root_cause": "Rebuild as a project-local C++/ATen extension using PrivateUse1 tensors.",
        "fix_steps": ["Use torch.utils.cpp_extension.CppExtension and ATen gather/scatter ops."],
    }), encoding="utf-8")
    store.upsert_index({
        "id": "metadata-clean-exp",
        "type": "document",
        "status": "staging",
        "title": "Require fail-closed per-unit custom-op final-gate evidence",
        "category": "other",
        "asset_paths": [str(stale_path)],
    })

    class SelectorSession:
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "metadata-clean-exp" in command
            return json.dumps({
                "selected_experiences": [{"id": "metadata-clean-exp", "type": "document"}],
                "summary": "selected stale body",
                "warning": "",
            })

    result = ExperienceQuerier(store, SelectorSession()).query({
        "phase": "analyze_error",
        "parent_phase": "phase_5_validation",
        "custom_op_native_gate_required": "true",
    })

    assert result["selected_experiences"] == []


def test_experience_query_keeps_aten_custom_op_outside_native_gate(tmp_path):
    store = ExperienceStore(str(tmp_path))
    stale_path = tmp_path / "aten.json"
    stale_path.write_text(json.dumps({"title": "ATen-only"}), encoding="utf-8")
    store.upsert_index({
        "id": "aten-exp",
        "type": "document",
        "status": "staging",
        "title": "Port CUDA custom extension to NPU-routed C++/ATen extension",
        "subtype": "cuda_extension_to_npu_routed_cpp_extension",
        "tags": ["custom-op", "aten", "cpp-extension"],
        "asset_paths": [str(stale_path)],
    })

    class SelectorSession:
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "aten-exp" in command
            return json.dumps({
                "selected_experiences": [{"id": "aten-exp", "type": "document"}],
                "summary": "allowed outside native gate",
                "warning": "",
            })

    result = ExperienceQuerier(store, SelectorSession()).query({"phase": "phase_1_project_analysis"})

    assert [exp["id"] for exp in result["selected_experiences"]] == ["aten-exp"]


def test_compact_catalog_default_is_dry_run(tmp_path):
    store = ExperienceStore(str(tmp_path))
    entries = [
        {"id": "promoted-same", "type": "skill", "status": "promoted", "title": "Old", "tags": ["a"]},
        {"id": "promoted-same", "type": "skill", "status": "promoted", "title": "New", "tags": ["b"]},
    ]
    store.registry._rewrite_catalog(entries)

    result = store.compact_catalog()

    assert result["dry_run"] is True
    assert result["removed_count"] == 1
    assert len(store.read_catalog()) == 2


def test_compact_catalog_dedupes_without_touching_legacy_index(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({"id": "legacy-only", "status": "staging"})
    entries = [
        {"id": "promoted-same", "type": "skill", "status": "promoted", "title": "Old", "tags": ["a"], "asset_paths": ["a.json"]},
        {"id": "promoted-same", "type": "skill", "status": "promoted", "title": "New", "tags": ["b"], "asset_paths": ["b.json"]},
    ]
    store.registry._rewrite_catalog(entries)

    result = store.compact_catalog(dry_run=False)

    assert result["dry_run"] is False
    assert result["removed_count"] == 1
    compacted = store.read_catalog()
    assert len(compacted) == 1
    assert compacted[0]["title"] == "New"
    assert compacted[0]["tags"] == ["a", "b"]
    assert store.read_index() == [{"id": "legacy-only", "status": "staging"}]


def test_cleanup_staging_dry_run_is_non_mutating(tmp_path):
    store = ExperienceStore(str(tmp_path))
    run_dir = tmp_path / "memory" / "staging" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "note.json").write_text("{}", encoding="utf-8")
    store.upsert_catalog_entry({
        "id": "promoted-npu-fix",
        "type": "skill",
        "status": "promoted",
        "title": "NPU Fix",
        "source_runs": ["run-1"],
    })

    result = store.cleanup_staging()

    assert result["dry_run"] is True
    assert result["target_count"] == 1
    assert run_dir.exists()


def test_integrity_reports_missing_asset_paths(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_catalog_entry({
        "id": "promoted-broken",
        "type": "skill",
        "status": "promoted",
        "title": "Broken",
        "asset_paths": [".memory/skills/missing/skill_data.json"],
    })

    report = store.validate_integrity()

    assert report["ok"] is False
    assert report["missing_asset_paths"] == [{"id": "promoted-broken", "path": ".memory/skills/missing/skill_data.json"}]


def test_rebuild_catalog_ignores_non_canonical_promotion_metadata_json(tmp_path):
    store = ExperienceStore(str(tmp_path))
    path = store.promote_from_staging("run-1", "document", {
        "type": "document",
        "title": "Migration Case",
        "category": "case_study",
        "tags": ["torch-npu"],
        "confidence": 0.8,
        "asset_names": ["document.md", "metadata.json"],
        "_asset_contents": {
            "document.md": "# Migration Case\n",
            "metadata.json": {"type": "document", "run_id": "run-1"},
        },
    })

    entries = store.rebuild_catalog()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["id"] == "promoted-migration-case"
    assert entry["type"] == "document"
    assert os.path.join("memory", "promotions", "knowledge", "migration-case", "experience.json") in entry["asset_paths"]
    assert os.path.join("memory", "promotions", "knowledge", "migration-case", "metadata.json") in entry["asset_paths"]
    assert path.endswith(os.path.join("memory", "promotions", "knowledge", "migration-case", "experience.json"))


def test_record_experience_usage_preserves_legacy_custom_fields(tmp_path):
    store = ExperienceStore(str(tmp_path))
    store.upsert_index({
        "id": "legacy-exp",
        "status": "promoted",
        "type": "skill",
        "custom_payload": {"keep": ["this"]},
        "legacy_only": "unchanged",
    })
    store.upsert_catalog_entry({"id": "legacy-exp", "status": "promoted", "type": "skill"})

    store.record_experience_usage(
        selected_ids=["legacy-exp"],
        used_ids=["legacy-exp"],
        verification={"experience_ids": ["legacy-exp"], "passed": False},
    )

    legacy_entry = store.read_index()[0]
    assert legacy_entry["custom_payload"] == {"keep": ["this"]}
    assert legacy_entry["legacy_only"] == "unchanged"
    assert legacy_entry["usage"]["selected_count"] == 1
    assert legacy_entry["usage"]["used_count"] == 1
    assert legacy_entry["usage"]["verification_failure_count"] == 1
    assert legacy_entry["failure_count"] == 1


def test_prune_orphans_preserves_assets_referenced_only_by_legacy_index(tmp_path):
    store = ExperienceStore(str(tmp_path))
    legacy_asset = tmp_path / "memory" / "cases" / "legacy.json"
    orphan_asset = tmp_path / "memory" / "cases" / "orphan.json"
    legacy_asset.parent.mkdir(parents=True, exist_ok=True)
    legacy_asset.write_text("{}", encoding="utf-8")
    orphan_asset.write_text("{}", encoding="utf-8")
    store.upsert_index({
        "id": "legacy-only",
        "status": "promoted",
        "type": "skill",
        "asset_paths": [os.path.join("memory", "cases", "legacy.json")],
    })

    dry_run = store.prune_orphans()
    result = store.prune_orphans(dry_run=False)

    assert dry_run["dry_run"] is True
    assert os.path.join("memory", "cases", "orphan.json") in dry_run["targets"]
    assert os.path.join("memory", "cases", "legacy.json") not in dry_run["targets"]
    assert legacy_asset.exists()
    assert not orphan_asset.exists()
    assert result["target_count"] == 1
