"""Failing-first tests for the V3-only legacy copy_artifacts filter (plan todo 4).

`_drop_legacy_copy_artifacts_hooks` must remove only `operation == "copy_artifacts"`
entries from `workflow.hooks["workflow_end"]`, preserving the relative order and
object identity of every surviving hook, and must be a no-op when workflow_end is
missing, empty, or already clean. Other hook points (e.g. workflow_start) are
never scanned: V1/V2/direct behavior stays unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.types import HookDefinition, WorkflowDefinition
from tests.e2e import e2e_test_v3


class TestMixedWorkflowEndHooks:
    def test_drops_only_copy_artifacts_preserving_order_and_object_values(
        self,
    ) -> None:
        # Given a workflow_end of [snapshot_project, copy_artifacts, write_summary]
        snapshot = HookDefinition(operation="snapshot_project", save_as="after_snapshot")
        legacy = HookDefinition(operation="copy_artifacts")
        summary = HookDefinition(
            operation="write_summary", critical=True, params={"key": "value"}
        )
        workflow = WorkflowDefinition(
            name="wf",
            version="1.0",
            hooks={"workflow_end": [snapshot, legacy, summary]},
        )

        # When the V3 runner drops legacy copy_artifacts hooks
        e2e_test_v3._drop_legacy_copy_artifacts_hooks(workflow)

        # Then survivors keep relative order, identity, and field values
        surviving = workflow.hooks["workflow_end"]
        assert [hook.operation for hook in surviving] == [
            "snapshot_project",
            "write_summary",
        ]
        assert surviving[0] is snapshot
        assert surviving[1] is summary
        assert surviving[0].save_as == "after_snapshot"
        assert surviving[1].critical is True
        assert surviving[1].params == {"key": "value"}


class TestAlreadyCleanWorkflowEnd:
    def test_workflow_end_without_copy_artifacts_is_left_identical(self) -> None:
        # Given an already-clean workflow_end hook list
        snapshot = HookDefinition(operation="snapshot_project")
        summary = HookDefinition(operation="write_summary")
        original_list: list[HookDefinition] = [snapshot, summary]
        workflow = WorkflowDefinition(
            name="wf", version="1.0", hooks={"workflow_end": original_list}
        )

        # When the filter runs
        e2e_test_v3._drop_legacy_copy_artifacts_hooks(workflow)

        # Then the same list object survives with identical content
        assert workflow.hooks["workflow_end"] is original_list
        assert [hook.operation for hook in workflow.hooks["workflow_end"]] == [
            "snapshot_project",
            "write_summary",
        ]


class TestMissingWorkflowEndHookPoint:
    def test_missing_workflow_end_is_a_no_op(self) -> None:
        # Given a workflow whose hooks have no workflow_end point at all
        start_hooks = [HookDefinition(operation="snapshot_project")]
        workflow = WorkflowDefinition(
            name="wf", version="1.0", hooks={"workflow_start": start_hooks}
        )

        # When the filter runs, it neither raises nor materializes the key
        e2e_test_v3._drop_legacy_copy_artifacts_hooks(workflow)

        # Then hooks are exactly as before
        assert "workflow_end" not in workflow.hooks
        assert workflow.hooks["workflow_start"] is start_hooks


class TestEmptyWorkflowEnd:
    def test_empty_workflow_end_is_a_no_op(self) -> None:
        # Given an explicitly empty workflow_end hook list
        empty_list: list[HookDefinition] = []
        workflow = WorkflowDefinition(
            name="wf", version="1.0", hooks={"workflow_end": empty_list}
        )

        # When the filter runs
        e2e_test_v3._drop_legacy_copy_artifacts_hooks(workflow)

        # Then the same empty list object survives untouched
        assert workflow.hooks["workflow_end"] is empty_list
        assert workflow.hooks["workflow_end"] == []


class TestOtherHookPointsUntouched:
    def test_copy_artifacts_in_workflow_start_is_not_filtered(self) -> None:
        # Given copy_artifacts declared at workflow_start AND workflow_end
        start_legacy = HookDefinition(operation="copy_artifacts")
        end_legacy = HookDefinition(operation="copy_artifacts")
        start_list: list[HookDefinition] = [start_legacy]
        workflow = WorkflowDefinition(
            name="wf",
            version="1.0",
            hooks={"workflow_start": start_list, "workflow_end": [end_legacy]},
        )

        # When the filter runs
        e2e_test_v3._drop_legacy_copy_artifacts_hooks(workflow)

        # Then only workflow_end is filtered; workflow_start keeps its entry
        assert workflow.hooks["workflow_end"] == []
        assert workflow.hooks["workflow_start"] is start_list
        assert workflow.hooks["workflow_start"][0] is start_legacy
