import json
import os

from core.experience_classifier import ExperienceClassifier
from core.experience_dispatcher import ExperienceDispatcher
from core.experience_refiner import ExperienceRefiner
from core.experience_store import ExperienceStore


class FakeExperienceSessionManager:
    def __init__(self):
        self.created_roles = []
        self.sent = []
        self.responses = {}

    def get_or_create(self, role: str, lifecycle: str) -> str:
        self.created_roles.append((role, lifecycle))
        return f"session:{role}"

    def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
        self.sent.append({"session_id": session_id, "command": command, "timeout": timeout})
        role = session_id.split(":", 1)[1]
        return self.responses.get(role, '{"type": "skill", "title": "Default skill"}')


def test_classifier_routes_rule_prompt_document_and_skill():
    classifier = ExperienceClassifier()

    assert (
        classifier.classify(
            {"recommended_type": "rule", "title": "Replace cuda", "confidence": 0.8}
        )["type"]
        == "rule"
    )
    assert (
        classifier.classify({"tags": ["prompt"], "problem_description": "Improve analyzer prompt"})[
            "type"
        ]
        == "prompt"
    )
    assert (
        classifier.classify({"category": "case_study", "title": "Migration case"})["type"]
        == "document"
    )
    skill = classifier.classify(
        {
            "category": "operator_incompat",
            "tags": ["torch-npu", "flash-attn"],
            "rough_fix_approach": "Replace flash attention with SDPA",
            "confidence": 0.9,
        }
    )
    assert skill["type"] == "skill"
    assert skill["target_roles"] == ["operator_fixer"]
    assert skill["solidifier"] == "skill_solidifier"
    assert skill["trigger_fingerprint"]


def test_classifier_uses_live_classifier_role_and_instruction_prompt():
    session_mgr = FakeExperienceSessionManager()
    session_mgr.responses["experience_classifier"] = json.dumps(
        {
            "type": "rule",
            "target_roles": ["code_adapter"],
            "target_phases": ["phase_4_rule_migration"],
            "solidifier": "rule_solidifier",
            "reasoning": "mechanical replacement",
            "confidence": 0.91,
            "trigger_fingerprint": "cuda|stream",
        }
    )

    result = ExperienceClassifier(session_mgr).classify(
        {
            "candidate_id": "c-rule",
            "recommended_type": "skill",
            "title": "Replace CUDA stream",
        }
    )

    assert result["type"] == "rule"
    assert ("experience_classifier", "persistent") in session_mgr.created_roles
    assert "Return exactly one JSON object" in session_mgr.sent[0]["command"]
    assert "Allowed solidifier values" in session_mgr.sent[0]["command"]


def test_refiner_uses_solidifier_role_and_caches_per_role(tmp_path):
    store = ExperienceStore(str(tmp_path))
    session_mgr = FakeExperienceSessionManager()
    session_mgr.responses["experience_rule_solidifier"] = json.dumps(
        {
            "type": "rule",
            "title": "Replace CUDA stream",
            "pattern": "torch.cuda.stream",
            "replacement": "torch.npu.stream",
            "category": "cuda_api",
            "tags": ["cuda", "npu"],
            "confidence": 0.9,
        }
    )
    refiner = ExperienceRefiner(str(tmp_path / "artifacts"), store, session_mgr)
    classification = {
        "type": "rule",
        "target_roles": ["code_adapter"],
        "target_phases": ["phase_4_rule_migration"],
        "solidifier": "rule_solidifier",
        "reasoning": "mechanical replacement",
        "confidence": 0.9,
        "trigger_fingerprint": "cuda|stream",
    }

    for run_id in ["run-1", "run-2"]:
        refined = refiner.refine(
            {
                "candidate_id": f"rule-{run_id}",
                "title": "Replace CUDA stream",
                "recommended_type": "rule",
                "category": "cuda_api",
                "tags": ["cuda", "npu"],
            },
            run_id,
            {},
            classification=classification,
        )
        assert refined["type"] == "rule"

    created_roles = [role for role, _ in session_mgr.created_roles]
    assert created_roles == ["experience_rule_solidifier"]
    assert [item["session_id"] for item in session_mgr.sent] == [
        "session:experience_rule_solidifier",
        "session:experience_rule_solidifier",
    ]


def test_refiner_uses_distinct_solidifier_sessions(tmp_path):
    store = ExperienceStore(str(tmp_path))
    session_mgr = FakeExperienceSessionManager()
    session_mgr.responses["experience_skill_solidifier"] = (
        '{"type": "skill", "title": "Skill", "fix_steps": ["Do it"]}'
    )
    session_mgr.responses["experience_document_solidifier"] = (
        '{"type": "document", "title": "Doc", "body": "Knowledge"}'
    )
    refiner = ExperienceRefiner(str(tmp_path / "artifacts"), store, session_mgr)

    refiner.refine(
        {"candidate_id": "s", "title": "Skill"},
        "run-s",
        {},
        classification={
            "type": "skill",
            "solidifier": "skill_solidifier",
            "target_roles": ["main_engineer"],
            "target_phases": ["phase_5_validation"],
            "reasoning": "test",
            "confidence": 0.8,
            "trigger_fingerprint": "skill",
        },
    )
    refiner.refine(
        {"candidate_id": "d", "title": "Doc"},
        "run-d",
        {},
        classification={
            "type": "document",
            "solidifier": "document_solidifier",
            "target_roles": ["main_engineer"],
            "target_phases": ["phase_6_report"],
            "reasoning": "test",
            "confidence": 0.8,
            "trigger_fingerprint": "doc",
        },
    )

    assert session_mgr.created_roles == [
        ("experience_skill_solidifier", "persistent"),
        ("experience_document_solidifier", "persistent"),
    ]


def test_skill_solidifier_writes_package_assets(tmp_path):
    store = ExperienceStore(str(tmp_path))
    refiner = ExperienceRefiner(str(tmp_path / "artifacts"), store, None)

    refined = refiner.refine(
        {
            "candidate_id": "c1",
            "recommended_type": "skill",
            "skill_name": "npu-flash-attn",
            "title": "Flash Attention NPU Fix",
            "category": "operator_incompat",
            "subtype": "flash_attention",
            "tags": ["torch-npu", "flash-attn"],
            "rough_fix_approach": "Replace flash_attn with SDPA",
            "confidence": 0.95,
            "references": ["phase_5_validation"],
        },
        "run-1",
        {},
    )

    refined_dir = tmp_path / "memory" / "staging" / "run-1" / "refined"
    assert refined["type"] == "skill"
    assert (refined_dir / "skill_data.json").is_file()
    assert (refined_dir / "SKILL.md").is_file()
    assert (refined_dir / "skill.yaml").is_file()
    assert (refined_dir / "verification.md").is_file()
    assert (refined_dir / "references" / "sources.md").is_file()


def test_dispatcher_classifies_before_refining_without_llm(tmp_path):
    store = ExperienceStore(str(tmp_path))
    dispatcher = ExperienceDispatcher(str(tmp_path / "artifacts"), store, None)

    results = dispatcher.dispatch_and_refine(
        "run-1",
        [
            {
                "candidate_id": "doc-1",
                "title": "Migration Case Study",
                "recommended_type": "document",
                "problem_description": "Narrative learning from a migration",
                "tags": ["case-study", "torch-npu"],
                "category": "case_study",
                "confidence": 0.8,
            }
        ],
    )

    assert results[0]["type"] == "document"
    assert results[0]["classifier"]["type"] == "document"
    assert "target_roles" in results[0]
    refined_dir = tmp_path / "memory" / "staging" / "run-1" / "refined"
    assert (refined_dir / "document.md").is_file()


def test_nested_asset_writes_are_safe(tmp_path):
    store = ExperienceStore(str(tmp_path))

    store.write_refined_experience(
        "run-1",
        {"title": "Nested"},
        {
            "references/source.md": "nested text",
            "examples/data.json": {"ok": True},
        },
    )

    refined_dir = tmp_path / "memory" / "staging" / "run-1" / "refined"
    assert (refined_dir / "references" / "source.md").read_text(encoding="utf-8") == "nested text\n"
    assert json.loads((refined_dir / "examples" / "data.json").read_text(encoding="utf-8")) == {
        "ok": True
    }


def test_list_json_assets_round_trip_as_valid_json_in_staging_and_promotion(tmp_path):
    store = ExperienceStore(str(tmp_path))
    code_changes = [{"file": "model.py", "before": ".cuda()", "after": ".npu()"}]

    store.write_refined_experience(
        "run-1",
        {"title": "List Asset"},
        {
            "examples/code_changes.json": code_changes,
        },
    )
    refined_asset = (
        tmp_path / "memory" / "staging" / "run-1" / "refined" / "examples" / "code_changes.json"
    )
    assert json.loads(refined_asset.read_text(encoding="utf-8")) == code_changes

    promoted_path = store.promote_from_staging(
        "run-1",
        "skill",
        {
            "skill_name": "list-json-skill",
            "title": "List JSON Skill",
            "category": "code",
            "tags": ["json"],
            "confidence": 0.8,
            "_asset_contents": {"examples/code_changes.json": code_changes},
        },
    )
    promoted_asset = (
        tmp_path / ".memory" / "skills" / "list-json-skill" / "examples" / "code_changes.json"
    )
    assert promoted_path.endswith(
        os.path.join(".memory", "skills", "list-json-skill", "skill_data.json")
    )
    assert json.loads(promoted_asset.read_text(encoding="utf-8")) == code_changes


def test_non_skill_promotion_paths_and_catalog_assets(tmp_path):
    store = ExperienceStore(str(tmp_path))
    cases = [
        ("document", "document.md", os.path.join("memory", "promotions", "knowledge")),
        ("rule", "rule.yaml", os.path.join("memory", "promotions", "rules")),
        ("prompt", "proposal.yaml", os.path.join("memory", "promotions", "prompt_proposals")),
    ]

    for exp_type, asset_name, expected_root in cases:
        path = store.promote_from_staging(
            "run-1",
            exp_type,
            {
                "type": exp_type,
                "title": f"{exp_type} item",
                "category": "memory",
                "subtype": exp_type,
                "tags": ["torch-npu", exp_type],
                "confidence": 0.8,
                "asset_names": [asset_name],
                "_asset_contents": {asset_name: f"{exp_type} asset"},
            },
        )
        assert expected_root in os.path.relpath(path, str(tmp_path))
        assert asset_name in os.listdir(os.path.dirname(path))

    index_types = {
        entry["type"] for entry in store.read_index() if entry.get("status") == "promoted"
    }
    catalog_types = {entry["type"] for entry in store.read_catalog()}
    assert {"document", "rule", "prompt"} <= index_types
    assert {"document", "rule", "prompt"} <= catalog_types
    assert store.validate_integrity()["ok"] is True


def test_auto_promote_preserves_non_skill_type(tmp_path):
    store = ExperienceStore(str(tmp_path))
    existing = {
        "id": "run-0-exp-doc",
        "type": "document",
        "status": "staging",
        "category": "case_study",
        "tags": ["torch-npu", "case"],
        "title": "Existing Case",
        "confidence": 0.7,
    }
    store.upsert_index(existing)

    promoted = store.check_and_auto_promote(
        {
            "type": "document",
            "category": "case_study",
            "tags": ["torch-npu", "case"],
            "title": "New Case",
            "confidence": 0.9,
            "_asset_contents": {"document.md": "# New Case\n"},
            "asset_names": ["document.md"],
        },
        "run-1",
    )

    assert promoted is True
    promoted_entries = [entry for entry in store.read_index() if entry.get("status") == "promoted"]
    assert promoted_entries[0]["type"] == "document"
    assert not (tmp_path / ".memory" / "skills" / "New Case").exists()


def test_legacy_skill_promotion_still_writes_expected_files(tmp_path):
    store = ExperienceStore(str(tmp_path))

    path = store.promote_from_staging(
        "run-1",
        "skill",
        {
            "skill_name": "legacy-skill",
            "title": "Legacy Skill",
            "category": "dependency",
            "subtype": "torch_npu_install",
            "tags": ["torch-npu", "pip"],
            "confidence": 0.9,
            "steps": ["Install CPU torch before torch-npu"],
        },
    )

    assert path.endswith(os.path.join(".memory", "skills", "legacy-skill", "skill_data.json"))
    assert (tmp_path / ".memory" / "skills" / "legacy-skill" / "SKILL.md").is_file()
    assert store.read_index()[0]["id"] == "promoted-legacy-skill"
    assert store.read_catalog()[0]["type"] == "skill"


def test_experience_query_prefilters_by_target_role(tmp_path):
    # pylint: disable-next=import-outside-toplevel; silent
    from core.experience_query import ExperienceQuerier

    store = ExperienceStore(str(tmp_path))
    matching_asset = tmp_path / "dependency.md"
    mismatched_asset = tmp_path / "operator.md"
    matching_asset.write_text("dependency guidance", encoding="utf-8")
    mismatched_asset.write_text("operator guidance", encoding="utf-8")
    store.upsert_index(
        {
            "id": "dep-exp",
            "type": "document",
            "title": "Dependency Fix",
            "status": "promoted",
            "target_roles": ["dependency_fixer"],
            "target_phases": ["phase_5_validation"],
            "tags": ["torch-npu"],
            "confidence": 0.9,
            "asset_paths": [str(matching_asset)],
        }
    )
    store.upsert_index(
        {
            "id": "op-exp",
            "type": "document",
            "title": "Operator Fix",
            "status": "promoted",
            "target_roles": ["operator_fixer"],
            "target_phases": ["phase_5_validation"],
            "tags": ["torch-npu"],
            "confidence": 0.95,
            "asset_paths": [str(mismatched_asset)],
        }
    )

    class SelectorSession:
        # pylint: disable-next=unused-argument; silent
        def get_or_create(self, role: str, lifecycle: str) -> str:
            return f"session:{role}"

        # pylint: disable-next=unused-argument; silent
        def send_command(self, session_id: str, command: str, timeout: int = 600) -> str:
            assert "dep-exp" in command
            assert "op-exp" not in command
            return json.dumps(
                {
                    "selected_experiences": [
                        {
                            "id": "dep-exp",
                            "type": "document",
                            "relevance_score": 0.91,
                            "reasoning": "role match",
                        }
                    ],
                    "summary": "selected dependency",
                    "warning": "",
                }
            )

    result = ExperienceQuerier(store, SelectorSession()).query(
        {
            "phase": "analyze_error",
            "role": "dependency_fixer",
            "parent_phase": "phase_5_validation",
            "tags": ["torch-npu"],
        },
        load_full=False,
    )

    assert [exp["id"] for exp in result["selected_experiences"]] == ["dep-exp"]
    assert result["selected_experiences"][0]["target_roles"] == ["dependency_fixer"]


def test_prefilter_index_keeps_role_match_and_filters_role_mismatch(tmp_path):
    # pylint: disable-next=import-outside-toplevel; silent
    from core.experience_query import ExperienceQuerier

    store = ExperienceStore(str(tmp_path))
    dependency_asset = tmp_path / "dependency.md"
    operator_asset = tmp_path / "operator.md"
    dependency_asset.write_text("dependency guidance", encoding="utf-8")
    operator_asset.write_text("operator guidance", encoding="utf-8")
    entries = [
        {
            "id": "dep-exp",
            "type": "document",
            "status": "promoted",
            "title": "Dependency Fix",
            "target_roles": ["dependency_fixer"],
            "target_phases": ["phase_5_validation"],
            "tags": ["torch-npu"],
            "confidence": 0.9,
            "asset_paths": [str(dependency_asset)],
        },
        {
            "id": "op-exp",
            "type": "document",
            "status": "promoted",
            "title": "Operator Fix",
            "target_roles": ["operator_fixer"],
            "target_phases": ["phase_5_validation"],
            "tags": ["torch-npu"],
            "confidence": 0.95,
            "asset_paths": [str(operator_asset)],
        },
    ]

    # pylint: disable-next=protected-access; silent
    filtered = ExperienceQuerier(store, None)._prefilter_index(
        entries,
        {
            "role": "dependency_fixer",
            "parent_phase": "phase_5_validation",
            "tags": ["torch-npu"],
        },
    )

    assert [entry["id"] for entry in filtered] == ["dep-exp"]
