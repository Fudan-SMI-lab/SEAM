from __future__ import annotations

from harness.session.opencode_contract import JsonObject


def reasoning_part(session_id: str, part_id: str = "prt_reasoning") -> JsonObject:
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": f"msg_assistant_{session_id}",
        "type": "reasoning",
        "text": "persisted accessible reasoning",
        "time": {"start": 1, "end": 2},
        "futureReasoning": {"raw": True},
    }


def task_part(session_id: str, child_id: str) -> JsonObject:
    return _tool_part(
        session_id,
        "prt_task",
        "task",
        {
            "status": "completed",
            "input": {"description": "child", "prompt": "inspect"},
            "output": "child result",
            "title": "Task child",
            "metadata": {
                "parentSessionId": session_id,
                "sessionId": child_id,
                "futureLineage": ["kept"],
            },
            "time": {"start": 2, "end": 3},
        },
    )


def overflow_part(session_id: str, part_id: str, output_path: str) -> JsonObject:
    return _tool_part(
        session_id,
        part_id,
        "read",
        {
            "status": "completed",
            "input": {"filePath": "large.txt"},
            "output": "... output truncated ...",
            "title": "Read output",
            "metadata": {"truncated": True, "outputPath": output_path},
            "time": {"start": 3, "end": 4},
        },
    )


def terminal_parts(session_id: str) -> list[JsonObject]:
    message_id = f"msg_assistant_{session_id}"
    return [
        reasoning_part(session_id),
        {
            "id": "prt_patch",
            "sessionID": session_id,
            "messageID": message_id,
            "type": "patch",
            "hash": "sha256:patch",
            "files": ["a.py"],
            "futurePatch": {"hunks": 1},
        },
        {
            "id": "prt_compaction",
            "sessionID": session_id,
            "messageID": message_id,
            "type": "compaction",
            "auto": True,
            "overflow": False,
            "futureCompaction": "kept",
        },
        _tool_part(
            session_id,
            "prt_error",
            "read",
            {
                "status": "error",
                "input": {"filePath": "missing"},
                "error": "missing",
                "metadata": {"attempt": 1},
                "time": {"start": 4, "end": 5},
            },
        ),
    ]


def _tool_part(
    session_id: str,
    part_id: str,
    tool: str,
    state: JsonObject,
) -> JsonObject:
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": f"msg_assistant_{session_id}",
        "type": "tool",
        "callID": f"call_{part_id}",
        "tool": tool,
        "state": state,
        "futureToolPart": {"preserved": True},
    }
