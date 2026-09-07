from __future__ import annotations

from harness.session.opencode_contract_json import JsonObject, JsonValue
from harness.session.opencode_contract_values import (
    finite_number,
    nonnegative_integer,
    object_value,
    optional_string,
    string_record,
    string_value,
    strings_present,
    valid_time,
    valid_tokens,
)


def _valid_source_text(source: JsonObject) -> bool:
    text = object_value(source.get("text"))
    return (
        text is not None
        and isinstance(text.get("value"), str)
        and finite_number(text.get("start"))
        and finite_number(text.get("end"))
    )


def _valid_file_source(value: JsonValue) -> bool:
    source = object_value(value)
    if source is None or not _valid_source_text(source):
        return False
    type_name = source.get("type")
    if type_name == "file":
        return isinstance(source.get("path"), str)
    if type_name == "resource":
        return strings_present(source, ("clientName", "uri"))
    if type_name != "symbol" or not strings_present(source, ("path", "name")):
        return False
    range_value = object_value(source.get("range"))
    start = object_value(range_value.get("start")) if range_value is not None else None
    end = object_value(range_value.get("end")) if range_value is not None else None
    return (
        nonnegative_integer(source.get("kind"))
        and start is not None
        and end is not None
        and all(nonnegative_integer(start.get(key)) for key in ("line", "character"))
        and all(nonnegative_integer(end.get(key)) for key in ("line", "character"))
    )


def valid_api_error(value: JsonValue) -> bool:
    error = object_value(value)
    data = object_value(error.get("data")) if error is not None else None
    if error is None or error.get("name") != "APIError" or data is None:
        return False
    return (
        isinstance(data.get("message"), str)
        and isinstance(data.get("isRetryable"), bool)
        and ("statusCode" not in data or nonnegative_integer(data.get("statusCode")))
        and (
            "responseHeaders" not in data or string_record(data.get("responseHeaders"))
        )
        and optional_string(data, "responseBody")
        and ("metadata" not in data or string_record(data.get("metadata")))
    )


def _valid_agent_source(value: JsonValue) -> bool:
    source = object_value(value)
    return (
        source is not None
        and isinstance(source.get("value"), str)
        and nonnegative_integer(source.get("start"))
        and nonnegative_integer(source.get("end"))
    )


def valid_known_part(type_name: str, raw: JsonObject) -> bool:
    if type_name == "snapshot":
        return isinstance(raw.get("snapshot"), str)
    if type_name == "patch":
        files = raw.get("files")
        return (
            isinstance(raw.get("hash"), str)
            and isinstance(files, list)
            and all(isinstance(item, str) for item in files)
        )
    if type_name == "file":
        return (
            strings_present(raw, ("mime", "url"))
            and optional_string(raw, "filename")
            and ("source" not in raw or _valid_file_source(raw.get("source")))
        )
    if type_name == "agent":
        return isinstance(raw.get("name"), str) and (
            "source" not in raw or _valid_agent_source(raw.get("source"))
        )
    if type_name == "compaction":
        return (
            isinstance(raw.get("auto"), bool)
            and ("overflow" not in raw or isinstance(raw.get("overflow"), bool))
            and (
                "tail_start_id" not in raw or isinstance(raw.get("tail_start_id"), str)
            )
        )
    if type_name == "subtask":
        model = raw.get("model")
        model_ok = "model" not in raw or (
            object_value(model) is not None
            and strings_present(object_value(model) or {}, ("providerID", "modelID"))
        )
        return (
            strings_present(raw, ("prompt", "description", "agent"))
            and model_ok
            and optional_string(raw, "command")
        )
    if type_name == "retry":
        time = object_value(raw.get("time"))
        return (
            nonnegative_integer(raw.get("attempt"))
            and valid_api_error(raw.get("error"))
            and time is not None
            and nonnegative_integer(time.get("created"))
        )
    if type_name == "step-start":
        return optional_string(raw, "snapshot")
    if type_name == "step-finish":
        return (
            isinstance(raw.get("reason"), str)
            and optional_string(raw, "snapshot")
            and finite_number(raw.get("cost"))
            and valid_tokens(raw.get("tokens"))
        )
    return False


def valid_tool_state(raw: JsonObject) -> bool:
    status = string_value(raw.get("status"))
    if not status:
        return False
    required = {
        "pending": {"input", "raw"},
        "running": {"input", "time"},
        "completed": {"input", "output", "title", "metadata", "time"},
        "error": {"input", "error", "time"},
    }.get(status)
    if required is None:
        return object_value(raw.get("input")) is not None
    if not required.issubset(raw) or object_value(raw.get("input")) is None:
        return False
    if status == "pending":
        return isinstance(raw.get("raw"), str)
    if not valid_time(raw.get("time"), require_end=status in {"completed", "error"}):
        return False
    metadata = raw.get("metadata")
    if "metadata" in raw and object_value(metadata) is None:
        return False
    if status == "running":
        return optional_string(raw, "title")
    if status == "error":
        return isinstance(raw.get("error"), str)
    attachments = raw.get("attachments")
    attachments_ok = "attachments" not in raw or (
        isinstance(attachments, list)
        and all(
            isinstance(item, dict)
            and strings_present(item, ("id", "sessionID", "messageID"))
            and item.get("type") == "file"
            and valid_known_part("file", item)
            for item in attachments
        )
    )
    return (
        isinstance(raw.get("output"), str)
        and isinstance(raw.get("title"), str)
        and object_value(metadata) is not None
        and attachments_ok
    )
