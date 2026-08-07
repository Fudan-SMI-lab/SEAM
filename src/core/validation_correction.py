from __future__ import annotations

import json
import re


def extract_output_format_from_prompt(prompt_text: object) -> str | None:
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        return None

    sections = re.finditer(
        r"^##[^\n]*Output Format[^\n]*\n(?P<body>.*?)(?=^##|\Z)",
        prompt_text,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    blocks: list[str] = []
    for section in sections:
        body = section.group("body")
        for match in re.finditer(
            r"```(?:json)?\s*\n(.*?)```", body, flags=re.IGNORECASE | re.DOTALL
        ):
            block = match.group(1).strip()
            if block:
                blocks.append(block)

    if not blocks:
        return None
    return "\n\n---\n\n".join(blocks)


def expected_output_format(output_schema: object, prompt_text: object) -> str | None:
    if isinstance(output_schema, dict) and output_schema:
        return json.dumps(output_schema, ensure_ascii=False, indent=2, default=str)
    return extract_output_format_from_prompt(prompt_text)


def extract_missing_fields(errors: list[str]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"field\s+['\"]([^'\"]+)['\"]",
        r"['\"]([^'\"]+)['\"]\s+(?:is\s+)?(?:required|missing)",
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s+must\s+be\s+a\b",
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s+must\s+be\s+an\b",
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s+must\s+be\s+non-empty\b",
    )
    for error in errors:
        for pattern in patterns:
            for match in re.finditer(pattern, error, flags=re.IGNORECASE):
                field = match.group(1).strip()
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)
    return fields


def build_validation_correction_prompt(
    error_msg: str,
    *,
    output_format_example: str | None = None,
    is_parse_failure: bool = False,
    phase_name: str = "",
    missing_fields: list[str] | None = None,
) -> str:
    phase_label = f" for {phase_name}" if phase_name else ""
    format_block = ""
    if output_format_example:
        format_block = (
            "\n\nExpected output format / final JSON shape or examples:\n```json\n"
            + output_format_example
            + "\n```"
        )

    field_hint = ""
    if missing_fields:
        field_hint = (
            "\n\nRequired or invalid fields called out by validation: "
            + ", ".join(missing_fields)
            + "."
        )

    action_hint = ""
    if "existing file for custom-op contracts" in error_msg:
        action_hint = (
            " Before returning corrected JSON, create or select the referenced "
            + "custom-op validation script so entry_script_path points to a real file."
        )

    final_contract = (
        "\n\nYou may reason, explain, or analyze before the JSON. "
        "A single parseable JSON object is mandatory. "
        "The last thing in your response must be one complete JSON object "
        "matching the expected shape. "
        "Do not put prose, markdown, or any other text after that final JSON object. "
        "Preserve previously-correct fields and only change what is needed "
        "to satisfy validation."
    )

    if is_parse_failure:
        return (
            f"Your previous response{phase_label} did not contain a valid JSON object. "
            "It may have contained prose/reasoning only, malformed JSON, or "
            "text after an incomplete JSON object."
            f"{format_block}"
            f"{final_contract}"
        )

    return (
        f"Your previous output{phase_label} failed validation. Error: {error_msg}."
        f"{action_hint}"
        f"{field_hint}"
        f"{format_block}"
        f"{final_contract}"
    )


def build_phase_correction_prompt(
    *,
    phase_name: str,
    validation_errors: list[str],
    output_format_example: str | None = None,
) -> str:
    error_msg = "; ".join(validation_errors) or "unknown validation failure"
    missing_fields = extract_missing_fields(validation_errors)
    return build_validation_correction_prompt(
        error_msg,
        output_format_example=output_format_example,
        phase_name=phase_name,
        missing_fields=missing_fields,
    )
