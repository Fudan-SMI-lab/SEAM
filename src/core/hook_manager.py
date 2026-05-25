"""Hook system for workflow lifecycle events."""

import json
import os
import shutil
import time
import warnings
from pathlib import Path
from typing import Any


class HookManager:
    """Execute builtin hooks at workflow lifecycle points."""

    def __init__(self, workflow_hooks: dict[str, list[dict]], output_dir: str = "."):
        """
        Args:
            workflow_hooks: {"workflow_start": [{"type": "builtin", "operation": "snapshot_project", ...}]}
            output_dir: directory for hook outputs (snapshots, telemetry, etc.)
        """
        self._hooks = workflow_hooks
        self._output_dir = output_dir
        self._results: dict[str, dict[str, Any]] = {}

    # ── Public API ──────────────────────────────────────────────────

    def execute(self, hook_point: str, context: dict) -> dict[str, Any]:
        """Execute all hooks for a given point.

        Returns merged results from all hooks.
        Non-critical hook failures are logged as warnings, not raised.
        """
        hooks = self._hooks.get(hook_point, [])
        point_results: dict[str, Any] = {}

        for hook_cfg in hooks:
            if isinstance(hook_cfg, dict):
                op_name = hook_cfg.get("operation", "")
                save_as = hook_cfg.get("save_as", op_name)
                critical = hook_cfg.get("critical", False)
            else:
                op_name = hook_cfg.operation
                save_as = hook_cfg.save_as if hook_cfg.save_as else op_name
                critical = hook_cfg.critical

            try:
                dispatch_params = hook_cfg if isinstance(hook_cfg, dict) else hook_cfg.params
                result = self._dispatch_builtin(op_name, dispatch_params, context)
                point_results[save_as] = result
            except Exception as e:
                if critical:
                    raise
                warnings.warn(
                    f"Hook '{op_name}' at '{hook_point}' failed (non-critical): {e}",
                    stacklevel=2,
                )
                point_results[save_as] = {"error": str(e), "success": False}

        # Accumulate into _results keyed by hook_point
        if hook_point not in self._results:
            self._results[hook_point] = {}
        self._results[hook_point].update(point_results)

        return point_results

    def get_results(self) -> dict[str, dict[str, Any]]:
        """Return all hook results accumulated so far."""
        return dict(self._results)

    def register(self, hook_point: str, phase_id: str = "", hook_config: list[dict] | None = None) -> None:
        """Register additional hooks at runtime.

        Args:
            hook_point: Lifecycle point (e.g. 'workflow_start', 'pre_phase', 'post_phase')
            phase_id: Optional phase ID to scope the hooks to
            hook_config: List of hook definitions to add
        """
        if hook_config is None:
            return
        key = f"{hook_point}:{phase_id}" if phase_id else hook_point
        if key not in self._hooks:
            self._hooks[key] = []
        self._hooks[key].extend(hook_config)


    # ── Dispatch ─────────────────────────────────────────────────────

    def _dispatch_builtin(self, operation: str, params: dict, context: dict) -> dict:
        """Route to the appropriate _builtin_{operation} method."""
        method_name = f"_builtin_{operation}"
        method = getattr(self, method_name, None)
        if method is None:
            raise ValueError(f"Unknown builtin operation: {operation}")
        return method(params, context)

    # ── Builtin operations ───────────────────────────────────────────

    def _builtin_snapshot_project(self, params: dict, context: dict) -> dict:
        """Snapshot all .py files in project_dir to JSON."""
        project_dir = params.get("project_dir") or context.get("PROJECT_DIR", "")
        if not project_dir:
            raise ValueError("project_dir is required for snapshot_project")

        project_path = Path(project_dir)
        if not project_path.is_dir():
            raise FileNotFoundError(f"project_dir does not exist: {project_dir}")

        snapshot = {}
        for py_file in project_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                stat = py_file.stat()
                rel = str(py_file.relative_to(project_path))
                snapshot[rel] = {
                    "content": content,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            except Exception as e:
                snapshot[str(py_file)] = {"error": str(e)}

        # Determine filename
        filename = "after_snapshot.json" if "workflow_end" in str(context.get("_hook_point", "")) else "before_snapshot.json"
        snap_path = os.path.join(self._output_dir, filename)
        os.makedirs(os.path.dirname(snap_path) if os.path.dirname(snap_path) else ".", exist_ok=True)

        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

        return {"snapshot_path": os.path.abspath(snap_path), "file_count": len(snapshot)}

    def _builtin_copy_artifacts(self, params: dict, context: dict) -> dict:
        """Copy .sm-artifacts/ directory to output_dir."""
        project_dir = params.get("project_dir") or context.get("PROJECT_DIR", "")
        if not project_dir:
            raise ValueError("project_dir is required for copy_artifacts")

        src = Path(project_dir) / ".sm-artifacts"
        dest = Path(self._output_dir) / ".sm-artifacts"

        if not src.exists():
            return {"artifacts_copied": False, "dest": str(dest), "reason": "source not found"}

        shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
        return {"artifacts_copied": True, "dest": str(dest)}

    def _builtin_write_summary(self, params: dict, context: dict) -> dict:
        """Write summary.json based on state and phase results."""
        summary = {
            "state": context.get("state", {}),
            "phase_results": context.get("phase_results", {}),
        }
        path = os.path.join(self._output_dir, "summary.json")
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        return {"summary_path": os.path.abspath(path)}

    def _builtin_save_telemetry(self, params: dict, context: dict) -> dict:
        """Save telemetry data. Delegate to context.get('telemetry_bridge').save_metrics()."""
        bridge = context.get("telemetry_bridge")
        telemetry_data = {}

        if bridge is not None and hasattr(bridge, "save_metrics"):
            try:
                bridge.save_metrics()
            except Exception:
                pass

            # Try to get metrics data if available
            if hasattr(bridge, "get_metrics"):
                try:
                    telemetry_data = bridge.get_metrics()
                except Exception:
                    pass

        path = os.path.join(self._output_dir, "telemetry.json")
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(telemetry_data, f, indent=2, ensure_ascii=False)

        return {"telemetry_saved": True, "path": os.path.abspath(path)}

    def _builtin_noop(self, params: dict, context: dict) -> dict:
        """No-operation hook (for testing/debugging)."""
        return {"noop": True, "timestamp": time.time()}
