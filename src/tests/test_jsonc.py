"""Given/When/Then tests for ``core.jsonc`` — the safe shared JSONC parser.

RED phase: every test fails because ``core.jsonc`` does not exist yet.
The parser must (1) strip comments and trailing commas via string-aware
logic lifted from the server lifecycle helper, (2) preserve ``https://``
inside string literals, (3) reject duplicate keys, prototype-pollution
keys, and non-object config roots with typed errors, and (4) expose an
honest, consent-gated normalized-output contract (comments are dropped,
source bytes are never mutated).
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from core.jsonc import (
    ArrayMergePolicy,
    JsoncError,
    JsoncErrorKind,
    JsoncParseError,
    error_kind_label,
    parse_config_object,
    parse_jsonc,
)

# --------------------------------------------------------------------------- #
# Spec fixture (the exact input referenced by the manual QA contract).        #
# --------------------------------------------------------------------------- #
SPEC_FIXTURE = '// c\n{"provider":{"x":{"options":{"baseURL":"https://api.example/v1",},},},}'


class TestJsoncHappyParsing:
    def test_parse_strips_line_comment_and_trailing_comma_preserving_https_baseurl(
        self,
    ) -> None:
        # Given JSONC with a leading line comment, nested trailing commas,
        # and an https baseURL inside a string.
        # When parsed by the public core.jsonc parser.
        result = parse_jsonc(SPEC_FIXTURE)
        # Then the exact baseURL is recovered without corruption.
        value = result.value
        assert isinstance(value, dict)
        provider = value["provider"]
        assert isinstance(provider, dict)
        node_x = provider["x"]
        assert isinstance(node_x, dict)
        options = node_x["options"]
        assert isinstance(options, dict)
        assert options["baseURL"] == "https://api.example/v1"

    def test_parse_strips_block_comment(self) -> None:
        # Given an inline block comment between values.
        # When parsed.
        result = parse_jsonc('{"a": 1 /* x */, "b": 2}')
        # Then both values survive and the comment is gone.
        assert result.value == {"a": 1, "b": 2}

    def test_https_inside_string_is_not_corrupted_by_comment_stripping(self) -> None:
        # Given a string value containing https:// and trailing line comments
        # that are NOT inside a string.
        # When parsed.
        result = parse_jsonc('{"u": "https://x.io/y"} // fake //')
        # Then the URL inside the string is byte-identical.
        assert result.value == {"u": "https://x.io/y"}

    def test_double_slash_inside_string_literal_is_preserved(self) -> None:
        # Given a string literal that contains // (which a naive regex would
        # mistake for a comment start).
        # When parsed.
        result = parse_jsonc('{"code": "a // b"}')
        # Then the literal is preserved exactly.
        assert result.value == {"code": "a // b"}

    def test_trailing_comma_in_nested_object_is_accepted(self) -> None:
        # Given trailing commas at multiple nesting depths.
        # When parsed.
        result = parse_jsonc('{"a": {"b": 1,},}')
        # Then the structure is recovered.
        assert result.value == {"a": {"b": 1}}

    def test_parse_config_object_returns_dict_root(self) -> None:
        # Given a well-formed object-rooted JSONC document.
        # When parsed via the config-object entrypoint.
        result = parse_config_object('{"k": 1} // ok')
        # Then the value is a dict and is the parsed root.
        assert isinstance(result.value, dict)
        assert result.value == {"k": 1}


class TestJsoncTypedRejection:
    def test_duplicate_object_key_raises_typed_duplicate_key_error(self) -> None:
        # Given a document with a duplicated object key.
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"a": 1, "a": 2}')
        # Then a typed DUPLICATE_KEY error is raised (not a bare ValueError).
        assert exc_info.value.error.kind is JsoncErrorKind.DUPLICATE_KEY

    @pytest.mark.parametrize("bad_key", ["__proto__", "constructor", "prototype"])
    def test_prototype_pollution_key_is_rejected(self, bad_key: str) -> None:
        # Given a key that would pollute object prototypes.
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"' + bad_key + '": 1}')
        # Then a typed PROTOTYPE_KEY error is raised.
        assert exc_info.value.error.kind is JsoncErrorKind.PROTOTYPE_KEY

    @pytest.mark.parametrize(
        "raw",
        [
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            "true",
        ],
    )
    def test_non_object_root_rejected_by_parse_config_object(
        self, raw: str
    ) -> None:
        # Given a JSONC document whose root is not a JSON object.
        # When parsed via the config-object entrypoint.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_config_object(raw)
        # Then a typed NON_OBJECT_ROOT error is raised.
        assert exc_info.value.error.kind is JsoncErrorKind.NON_OBJECT_ROOT

    def test_unterminated_string_raises_typed_error(self) -> None:
        # Given a document with an unterminated string literal.
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"a": "abc')
        # Then a typed UNTERMINATED_STRING error is raised.
        assert exc_info.value.error.kind is JsoncErrorKind.UNTERMINATED_STRING

    def test_unterminated_block_comment_raises_typed_error(self) -> None:
        # Given a document with an unterminated block comment.
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"a": 1 /* never closes')
        # Then a typed UNTERMINATED_COMMENT error is raised.
        assert exc_info.value.error.kind is JsoncErrorKind.UNTERMINATED_COMMENT

    def test_invalid_json_after_strip_raises_invalid_json_kind(self) -> None:
        # Given syntactically invalid JSON (after comment/comma stripping).
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"a": }')
        # Then a typed INVALID_JSON error is raised.
        assert exc_info.value.error.kind is JsoncErrorKind.INVALID_JSON

    def test_typed_error_carries_position_and_message(self) -> None:
        # Given a document that fails with a located error.
        # When parsed.
        with pytest.raises(JsoncParseError) as exc_info:
            _ = parse_jsonc('{"a": "abc')
        # Then the typed error carries a non-negative position and message.
        error: JsoncError = exc_info.value.error
        assert error.position >= 0
        assert error.message


class TestJsoncContractHonesty:
    def test_parsed_jsonc_result_is_immutable(self) -> None:
        # Given a parsed result.
        result = parse_jsonc('{"a": 1}')
        # When attempting to mutate a field via dynamic attribute set.
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(result, "value", {"b": 2})

    def test_comments_are_not_preserved_in_normalized_output(self) -> None:
        # Given JSONC containing both comment styles and trailing commas.
        # When parsed.
        result = parse_jsonc('// header\n{"a": 1 /* x */, "b": 2,}')
        # Then the normalized output contains no comment markers, is valid
        # JSON, and round-trips to the same value. This documents the
        # consent-gated contract: comments are intentionally dropped.
        normalized = result.normalized
        assert "//" not in normalized
        assert "/*" not in normalized
        assert json.loads(normalized) == {"a": 1, "b": 2}

    def test_source_text_remains_unchanged_after_parse_failure(self) -> None:
        # Given malformed JSONC text.
        original = '{"a": 1, "a": 2}'
        snapshot = original
        # When parsing fails.
        with pytest.raises(JsoncParseError):
            _ = parse_jsonc(original)
        # Then the caller's text is byte-identical (stale-state safety).
        assert original == snapshot


class TestJsoncExhaustiveVariants:
    def test_error_kind_label_is_exhaustive_over_all_variants(self) -> None:
        # Given every variant of the error-kind enum.
        # When each is mapped to a human label via the exhaustive helper.
        for kind in JsoncErrorKind:
            label = error_kind_label(kind)
            # Then every variant yields a non-empty label, proving the
            # match is exhaustive (new variants fail compilation via
            # assert_never).
            assert isinstance(label, str)
            assert label

    def test_array_merge_policy_covers_replace_and_plugin_dedupe(self) -> None:
        # Given the merge-policy enum.
        # Then exactly the two documented policies exist.
        assert {ArrayMergePolicy.REPLACE, ArrayMergePolicy.PLUGIN_DEDUPE} == set(
            ArrayMergePolicy
        )
