from __future__ import annotations

from harness.session.opencode_contract_json import JsonObject
from harness.session.opencode_contract_models import (
    CompletedToolState,
    ErrorToolState,
    KnownPart,
    Part,
    PendingToolState,
    ReasoningPart,
    RunningToolState,
    TaskLineage,
    TextPart,
    ToolPart,
    ToolState,
    UnknownPart,
    UnknownToolState,
)
from harness.session.opencode_contract_part_validation import (
    valid_known_part,
    valid_tool_state,
)
from harness.session.opencode_contract_values import (
    object_value,
    optional_boolean,
    optional_string,
    string_value,
    strings_present,
    valid_identifier,
    valid_time,
)


def _tool_state(raw: JsonObject) -> ToolState:
    status = string_value(raw.get("status"))
    if status == "pending":
        return PendingToolState(raw)
    if status == "running":
        return RunningToolState(raw)
    if status == "completed":
        return CompletedToolState(raw)
    if status == "error":
        return ErrorToolState(raw)
    return UnknownToolState(status, raw)


def _output_paths(state: JsonObject) -> tuple[str, ...]:
    found: list[str] = []
    containers = [
        state,
        object_value(state.get("metadata")),
        object_value(state.get("output")),
    ]
    for container in containers:
        if container is None:
            continue
        output_path = container.get("outputPath")
        if isinstance(output_path, str):
            found.append(output_path)
        output_paths = container.get("outputPaths")
        if isinstance(output_paths, list):
            found.extend(item for item in output_paths if isinstance(item, str))
    return tuple(dict.fromkeys(found))


def _task_lineage(
    state: ToolState,
    metadata: JsonObject | None,
    containing_session_id: str,
) -> tuple[TaskLineage | None, bool, bool]:
    if metadata is not None and not _valid_task_metadata(metadata):
        return None, False, True
    has_parent = metadata is not None and "parentSessionId" in metadata
    has_child = metadata is not None and "sessionId" in metadata
    if not has_parent and not has_child:
        return None, isinstance(state, CompletedToolState), False
    if metadata is None or not has_parent or not has_child:
        return None, False, True
    parent = metadata.get("parentSessionId")
    child = metadata.get("sessionId")
    if (
        not valid_identifier(parent, "ses")
        or not valid_identifier(child, "ses")
        or parent == child
        or parent != containing_session_id
    ):
        return None, False, True
    lineage = TaskLineage(string_value(parent), string_value(child), metadata)
    return lineage, False, False


def _valid_task_metadata(metadata: JsonObject) -> bool:
    model = object_value(metadata.get("model")) if "model" in metadata else None
    output_paths = metadata.get("outputPaths")
    return (
        (
            "model" not in metadata
            or model is not None
            and strings_present(model, ("providerID", "modelID"))
        )
        and optional_boolean(metadata, "background")
        and optional_string(metadata, "jobId")
        and optional_boolean(metadata, "truncated")
        and optional_string(metadata, "outputPath")
        and (
            "outputPaths" not in metadata
            or isinstance(output_paths, list)
            and all(isinstance(path, str) for path in output_paths)
        )
    )


def _text_part(raw: JsonObject, type_name: str) -> tuple[Part, bool, bool]:
    text = raw.get("text")
    if not isinstance(text, str):
        return UnknownPart(type_name, raw), False, True
    if not optional_boolean(raw, "synthetic") or not optional_boolean(raw, "ignored"):
        return UnknownPart(type_name, raw), False, True
    if "metadata" in raw and object_value(raw.get("metadata")) is None:
        return UnknownPart(type_name, raw), False, True
    time = raw.get("time")
    if type_name == "reasoning" and not valid_time(time):
        return UnknownPart(type_name, raw), False, True
    if type_name == "text" and "time" in raw and not valid_time(time):
        return UnknownPart(type_name, raw), False, True
    if type_name == "text":
        return TextPart(text, raw), False, False
    return ReasoningPart(text, raw), False, False


def parse_part(
    raw: JsonObject,
    containing_session_id: str,
) -> tuple[Part, bool, bool]:
    type_name = string_value(raw.get("type"))
    if type_name in {"text", "reasoning"}:
        return _text_part(raw, type_name)
    if type_name != "tool":
        known_types = {
            "subtask",
            "file",
            "step-start",
            "step-finish",
            "snapshot",
            "patch",
            "agent",
            "retry",
            "compaction",
        }
        if type_name in known_types:
            if valid_known_part(type_name, raw):
                return KnownPart(type_name, raw), False, False
            return UnknownPart(type_name, raw), False, True
        return UnknownPart(type_name, raw), True, False
    state_raw = object_value(raw.get("state"))
    tool = raw.get("tool")
    call_id = raw.get("callID")
    if (
        state_raw is None
        or not isinstance(tool, str)
        or not isinstance(call_id, str)
        or ("metadata" in raw and object_value(raw.get("metadata")) is None)
        or not valid_tool_state(state_raw)
    ):
        return UnknownPart(type_name, raw), False, True
    state = _tool_state(state_raw)
    metadata = object_value(state_raw.get("metadata"))
    lineage, lineage_partial, lineage_bad = (
        _task_lineage(state, metadata, containing_session_id)
        if tool == "task"
        else (None, False, False)
    )
    paths = _output_paths(state_raw)
    partial = isinstance(state, UnknownToolState) or bool(paths) or lineage_partial
    truncated = metadata is not None and metadata.get("truncated") is True
    bad = lineage_bad or truncated and not paths
    return ToolPart(tool, state, lineage, paths, raw), partial, bad
