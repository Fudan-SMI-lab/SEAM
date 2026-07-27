from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from core.config import load_workflow

SRC_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = SRC_ROOT / "workflows"
PROMPTS_DIR = SRC_ROOT / "prompts"


def test_active_review_prompts_declare_closed_verdict_domain() -> None:
    # Given
    selector = yaml.safe_load(
        (WORKFLOWS_DIR / "seam_auto_default.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(selector, dict)
    candidates = selector["candidate_workflows"]
    assert isinstance(candidates, list)
    review_templates: set[str] = set()
    for candidate in candidates:
        assert isinstance(candidate, dict)
        candidate_path = candidate["path"]
        assert isinstance(candidate_path, str)
        workflow = load_workflow(str(WORKFLOWS_DIR / candidate_path))
        for sub_workflow in workflow.sub_workflows.values():
            for phase in sub_workflow.phases:
                if isinstance(phase, dict) and phase.get("type") == "review":
                    template = phase.get("prompt_template")
                    assert isinstance(template, str)
                    review_templates.add(template)

    # When
    verdict_domains: dict[str, set[str]] = {}
    for template in review_templates:
        prompt = (PROMPTS_DIR / f"{template}.md").read_text(encoding="utf-8")
        documents = [
            json.loads(block)
            for block in re.findall(r"```json\s*(\{.*?\})\s*```", prompt, re.DOTALL)
        ]
        verdict_domains[template] = {
            document["verdict"]
            for document in documents
            if isinstance(document, dict) and isinstance(document.get("verdict"), str)
        }

    # Then
    assert review_templates == {
        "phase_5_review_container",
        "phase_5_review_container_musa",
        "phase_5_review_container_ppu",
    }
    assert all(domain == {"accept | reject"} for domain in verdict_domains.values())
