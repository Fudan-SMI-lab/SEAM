from __future__ import annotations

from harness.session.opencode_contract_json import JsonObject, JsonValue
from harness.session.opencode_contract_models import (
    CapabilityState,
    ChildSession,
    ChildrenResult,
    Completeness,
)
from harness.session.opencode_contract_values import (
    finite_number,
    nonnegative_integer,
    object_value,
    optional_string,
    response_status,
    strings_present,
    valid_diffs,
    valid_identifier,
    valid_permissions,
    valid_tokens,
)


def _valid_child_optional(raw: JsonObject, time: JsonObject) -> bool:
    summary = raw.get("summary")
    summary_value = object_value(summary) if "summary" in raw else None
    summary_ok = "summary" not in raw or (
        summary_value is not None
        and all(
            finite_number(summary_value.get(key))
            for key in ("additions", "deletions", "files")
        )
        and ("diffs" not in summary_value or valid_diffs(summary_value.get("diffs")))
    )
    share_value = object_value(raw.get("share")) if "share" in raw else None
    model_value = object_value(raw.get("model")) if "model" in raw else None
    revert_value = object_value(raw.get("revert")) if "revert" in raw else None
    return (
        optional_string(raw, "workspaceID")
        and optional_string(raw, "path")
        and optional_string(raw, "agent")
        and ("cost" not in raw or finite_number(raw.get("cost")))
        and ("tokens" not in raw or valid_tokens(raw.get("tokens")))
        and (
            "share" not in raw
            or (share_value is not None and strings_present(share_value, ("url",)))
        )
        and (
            "model" not in raw
            or (
                model_value is not None
                and strings_present(model_value, ("id", "providerID"))
                and optional_string(model_value, "variant")
            )
        )
        and ("metadata" not in raw or object_value(raw.get("metadata")) is not None)
        and ("permission" not in raw or valid_permissions(raw.get("permission")))
        and (
            "revert" not in raw
            or (
                revert_value is not None
                and isinstance(revert_value.get("messageID"), str)
                and all(
                    optional_string(revert_value, key)
                    for key in ("partID", "snapshot", "diff")
                )
            )
        )
        and summary_ok
        and ("compacting" not in time or nonnegative_integer(time.get("compacting")))
        and ("archived" not in time or finite_number(time.get("archived")))
    )


def parse_children(response: JsonObject | None) -> tuple[ChildrenResult, bool]:
    if response is None:
        return (
            ChildrenResult(
                (), CapabilityState.UNKNOWN, Completeness.INCOMPATIBLE, None
            ),
            True,
        )
    status = response_status(response)
    if status is None:
        return (
            ChildrenResult(
                (), CapabilityState.UNKNOWN, Completeness.INCOMPATIBLE, response
            ),
            True,
        )
    body = response.get("body")
    if status in (404, 405):
        return (
            ChildrenResult(
                (), CapabilityState.UNSUPPORTED, Completeness.UNSUPPORTED, body
            ),
            False,
        )
    if status != 200 or not isinstance(body, list):
        return (
            ChildrenResult((), CapabilityState.UNKNOWN, Completeness.PARTIAL, body),
            False,
        )
    sessions: list[ChildSession] = []
    seen_ids: set[str] = set()
    expected_parent: str | None = None
    required_strings = (
        "id",
        "slug",
        "projectID",
        "directory",
        "parentID",
        "title",
        "version",
    )
    for raw_session in body:
        if not isinstance(raw_session, dict):
            return _invalid_children(sessions, body)
        session_id = raw_session.get("id")
        parent_id = raw_session.get("parentID")
        time = object_value(raw_session.get("time"))
        if (
            not strings_present(raw_session, required_strings)
            or not isinstance(session_id, str)
            or not isinstance(parent_id, str)
            or not valid_identifier(session_id, "ses")
            or not valid_identifier(parent_id, "ses")
            or session_id == parent_id
            or session_id in seen_ids
            or expected_parent is not None
            and parent_id != expected_parent
            or time is None
            or not nonnegative_integer(time.get("created"))
            or not nonnegative_integer(time.get("updated"))
            or (
                "metadata" in raw_session
                and object_value(raw_session.get("metadata")) is None
            )
            or not _valid_child_optional(raw_session, time)
        ):
            return _invalid_children(sessions, body)
        seen_ids.add(session_id)
        expected_parent = parent_id
        sessions.append(ChildSession(session_id, parent_id, raw_session))
    return (
        ChildrenResult(
            tuple(sessions), CapabilityState.SUPPORTED, Completeness.COMPLETE, body
        ),
        False,
    )


def _invalid_children(
    sessions: list[ChildSession], body: list[JsonValue]
) -> tuple[ChildrenResult, bool]:
    return (
        ChildrenResult(
            tuple(sessions), CapabilityState.SUPPORTED, Completeness.INCOMPATIBLE, body
        ),
        True,
    )


def parse_document(
    response: JsonObject | None,
) -> tuple[CapabilityState, JsonObject | None, bool]:
    if response is None:
        return CapabilityState.UNKNOWN, None, False
    status = response_status(response)
    if status is None:
        return CapabilityState.UNKNOWN, None, True
    if status in (404, 405):
        return CapabilityState.UNSUPPORTED, None, False
    body = object_value(response.get("body"))
    paths = object_value(body.get("paths")) if body is not None else None
    if status != 200:
        return CapabilityState.UNKNOWN, None, False
    if paths is None:
        return CapabilityState.UNKNOWN, None, True
    return CapabilityState.SUPPORTED, paths, False
