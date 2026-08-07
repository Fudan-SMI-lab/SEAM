from __future__ import annotations

from typing import Final

from harness.session.opencode_contract_json import JsonObject, JsonValue
from harness.session.opencode_contract_models import (
    Completeness,
    MessageWithParts,
    MessagesResult,
    Part,
)
from harness.session.opencode_contract_part_validation import valid_api_error
from harness.session.opencode_contract_parts import parse_part
from harness.session.opencode_contract_values import (
    finite_number,
    nonnegative_integer,
    nonnegative_number,
    object_value,
    optional_string,
    response_status,
    string_value,
    strings_present,
    valid_diffs,
    valid_identifier,
    valid_tokens,
)


def _valid_assistant_error(value: JsonValue) -> bool:
    error = object_value(value)
    data = object_value(error.get("data")) if error is not None else None
    if error is None or data is None or not isinstance(error.get("name"), str):
        return False
    name = error.get("name")
    if name == "APIError":
        return valid_api_error(value)
    if name == "MessageOutputLengthError":
        return True
    if name == "ProviderAuthError":
        return strings_present(data, ("providerID", "message"))
    if name == "StructuredOutputError":
        return isinstance(data.get("message"), str) and nonnegative_integer(
            data.get("retries")
        )
    if name == "UnknownError":
        return isinstance(data.get("message"), str) and optional_string(data, "ref")
    if name == "ContextOverflowError":
        return isinstance(data.get("message"), str) and optional_string(
            data, "responseBody"
        )
    if name in {"MessageAbortedError", "ContentFilterError"}:
        return isinstance(data.get("message"), str)
    return False


def _valid_user_format(value: JsonValue) -> bool:
    format_value = object_value(value)
    if format_value is None:
        return False
    type_name = format_value.get("type")
    if type_name == "text":
        return True
    return (
        type_name == "json_schema"
        and object_value(format_value.get("schema")) is not None
        and (
            "retryCount" not in format_value
            or nonnegative_integer(format_value.get("retryCount"))
        )
    )


def _valid_user_optional(info: JsonObject) -> bool:
    summary = info.get("summary")
    summary_value = object_value(summary) if "summary" in info else None
    summary_ok = "summary" not in info or (
        summary_value is not None
        and optional_string(summary_value, "title")
        and optional_string(summary_value, "body")
        and valid_diffs(summary_value.get("diffs"))
    )
    tools = info.get("tools")
    tools_value = object_value(tools) if "tools" in info else None
    tools_ok = "tools" not in info or (
        tools_value is not None
        and all(isinstance(value, bool) for value in tools_value.values())
    )
    return (
        ("format" not in info or _valid_user_format(info.get("format")))
        and summary_ok
        and optional_string(info, "system")
        and tools_ok
    )


def _valid_assistant_optional(info: JsonObject) -> bool:
    return (
        ("error" not in info or _valid_assistant_error(info.get("error")))
        and ("summary" not in info or isinstance(info.get("summary"), bool))
        and optional_string(info, "variant")
        and optional_string(info, "finish")
    )


def _valid_info(info: JsonObject) -> bool:
    role = info.get("role")
    if not isinstance(role, str) or role not in {"user", "assistant"}:
        return False
    if not strings_present(info, ("id", "sessionID", "agent")):
        return False
    if not valid_identifier(info.get("id"), "msg"):
        return False
    if not valid_identifier(info.get("sessionID"), "ses"):
        return False
    time = object_value(info.get("time"))
    if time is None:
        return False
    if role == "user":
        model = object_value(info.get("model"))
        return (
            nonnegative_number(time.get("created"))
            and "completed" not in time
            and model is not None
            and strings_present(model, ("providerID", "modelID"))
            and optional_string(model, "variant")
            and _valid_user_optional(info)
        )
    if not nonnegative_integer(time.get("created")):
        return False
    if "completed" in time and not nonnegative_integer(time.get("completed")):
        return False
    if not strings_present(info, ("parentID", "modelID", "providerID", "mode")):
        return False
    if not valid_identifier(info.get("parentID"), "msg"):
        return False
    path = object_value(info.get("path"))
    return (
        path is not None
        and strings_present(path, ("cwd", "root"))
        and finite_number(info.get("cost"))
        and valid_tokens(info.get("tokens"))
        and _valid_assistant_optional(info)
    )


def _valid_part_base(raw: JsonObject, info: JsonObject) -> bool:
    return (
        strings_present(raw, ("id", "sessionID", "messageID", "type"))
        and valid_identifier(raw.get("id"), "prt")
        and raw.get("sessionID") == info.get("sessionID")
        and raw.get("messageID") == info.get("id")
    )


def _invalid_parent_graph(
    parents: dict[str, str], message_ids: set[str], full: bool
) -> bool:
    if full and any(parent not in message_ids for parent in parents.values()):
        return True
    resolved: set[str] = set()
    for start in parents:
        current = start
        path: set[str] = set()
        while current in parents and current not in resolved:
            if current in path:
                return True
            path.add(current)
            current = parents[current]
        resolved.update(path)
    return False


def parse_messages(response: JsonObject | None) -> tuple[MessagesResult, bool]:
    if response is None or response_status(response) != 200:
        return MessagesResult((), Completeness.INCOMPATIBLE, False), True
    body = response.get("body")
    query = object_value(response.get("query"))
    headers = object_value(response.get("headers"))
    if not isinstance(body, list) or query is None or headers is None:
        return MessagesResult((), Completeness.INCOMPATIBLE, False), True
    if len(body) > MAX_MESSAGE_COUNT:
        return MessagesResult((), Completeness.INCOMPATIBLE, False), True
    limit = query.get("limit")
    if "limit" in query and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
    ):
        return MessagesResult((), Completeness.INCOMPATIBLE, False), True
    has_cursor = any(key.casefold() == "x-next-cursor" for key in headers)
    full = limit in (None, 0) and not has_cursor
    parsed: list[MessageWithParts] = []
    seen_message_ids: set[str] = set()
    seen_part_ids: set[str] = set()
    parents: dict[str, str] = {}
    expected_session_id: str | None = None
    partial = not full
    part_count = 0
    for raw_message in body:
        if not isinstance(raw_message, dict):
            return MessagesResult(tuple(parsed), Completeness.INCOMPATIBLE, full), True
        info = object_value(raw_message.get("info"))
        raw_parts = raw_message.get("parts")
        if info is None or not _valid_info(info) or not isinstance(raw_parts, list):
            return MessagesResult(tuple(parsed), Completeness.INCOMPATIBLE, full), True
        part_count += len(raw_parts)
        if part_count > MAX_PART_COUNT:
            return MessagesResult(tuple(parsed), Completeness.INCOMPATIBLE, full), True
        message_id = string_value(info.get("id"))
        session_id = string_value(info.get("sessionID"))
        if (
            message_id in seen_message_ids
            or expected_session_id is not None
            and session_id != expected_session_id
        ):
            return MessagesResult(tuple(parsed), Completeness.INCOMPATIBLE, full), True
        seen_message_ids.add(message_id)
        expected_session_id = session_id
        if info.get("role") == "assistant":
            parents[message_id] = string_value(info.get("parentID"))
        parts: list[Part] = []
        for raw_part in raw_parts:
            if not isinstance(raw_part, dict) or not _valid_part_base(raw_part, info):
                return MessagesResult(
                    tuple(parsed), Completeness.INCOMPATIBLE, full
                ), True
            part_id = string_value(raw_part.get("id"))
            if part_id in seen_part_ids:
                return MessagesResult(
                    tuple(parsed), Completeness.INCOMPATIBLE, full
                ), True
            seen_part_ids.add(part_id)
            part, part_partial, part_bad = parse_part(
                raw_part, string_value(info.get("sessionID"))
            )
            parts.append(part)
            partial = partial or part_partial
            if part_bad:
                return MessagesResult(
                    tuple(parsed), Completeness.INCOMPATIBLE, full
                ), True
        parsed.append(MessageWithParts(info, tuple(parts), raw_message))
    if _invalid_parent_graph(parents, seen_message_ids, full):
        return MessagesResult(tuple(parsed), Completeness.INCOMPATIBLE, full), True
    completeness = Completeness.PARTIAL if partial else Completeness.COMPLETE
    return MessagesResult(tuple(parsed), completeness, full), False


MAX_MESSAGE_COUNT: Final = 100_000
MAX_PART_COUNT: Final = 1_000_000
