"""Given/When/Then tests for the structural config merge in ``core.jsonc``.

The merge must be deterministic: objects merge recursively, scalar values
replace, and arrays use field-specific policies where the ``plugin`` field
deduplicates entries while preserving order and exact string/tuple forms;
every other array replaces wholesale.
"""
from __future__ import annotations

import copy

from core.jsonc import JsonValue, merge_config


class TestObjectMergeSemantics:
    def test_objects_merge_recursively(self) -> None:
        # Given nested objects sharing a key.
        base: dict[str, JsonValue] = {"a": {"b": 1, "c": 2}}
        overrides: dict[str, JsonValue] = {"a": {"c": 3, "d": 4}}
        # When merged.
        merged = merge_config(base, overrides)
        # Then the shared object is recursively merged.
        assert merged == {"a": {"b": 1, "c": 3, "d": 4}}

    def test_scalar_in_overrides_replaces_base_value(self) -> None:
        base: dict[str, JsonValue] = {"a": 1}
        overrides: dict[str, JsonValue] = {"a": 2}
        merged = merge_config(base, overrides)
        assert merged == {"a": 2}

    def test_key_only_in_overrides_is_added(self) -> None:
        base: dict[str, JsonValue] = {"a": 1}
        overrides: dict[str, JsonValue] = {"b": 2}
        merged = merge_config(base, overrides)
        assert merged == {"a": 1, "b": 2}

    def test_key_only_in_base_is_kept(self) -> None:
        base: dict[str, JsonValue] = {"a": 1, "b": 2}
        overrides: dict[str, JsonValue] = {"b": 3}
        merged = merge_config(base, overrides)
        assert merged == {"a": 1, "b": 3}


class TestArrayMergePolicy:
    def test_non_plugin_array_is_replaced_not_merged(self) -> None:
        # Given a non-plugin array in both sides.
        base: dict[str, JsonValue] = {"tags": ["a", "b"]}
        overrides: dict[str, JsonValue] = {"tags": ["c"]}
        # When merged.
        merged = merge_config(base, overrides)
        # Then the override array replaces the base array entirely.
        assert merged == {"tags": ["c"]}

    def test_plugin_array_dedupes_equal_strings_preserving_first_order(
        self,
    ) -> None:
        # Given plugin arrays of string entries with an overlap.
        base: dict[str, JsonValue] = {"plugin": ["foo", "bar"]}
        overrides: dict[str, JsonValue] = {"plugin": ["bar", "baz"]}
        # When merged.
        merged = merge_config(base, overrides)
        # Then duplicates collapse and first-seen order is preserved.
        assert merged == {"plugin": ["foo", "bar", "baz"]}

    def test_plugin_array_dedupes_equal_tuples_preserving_exact_form(
        self,
    ) -> None:
        # Given plugin arrays of tuple (JSON array) entries.
        base: dict[str, JsonValue] = {"plugin": [["local", "./p"]]}
        overrides: dict[str, JsonValue] = {
            "plugin": [["local", "./p"], ["local", "./q"]]
        }
        # When merged.
        merged = merge_config(base, overrides)
        # Then equal tuples collapse to one, exact form preserved.
        assert merged == {"plugin": [["local", "./p"], ["local", "./q"]]}

    def test_plugin_array_keeps_distinct_string_and_tuple_forms_separate(
        self,
    ) -> None:
        # Given a string entry and a tuple entry that happen to share text.
        base: dict[str, JsonValue] = {"plugin": ["foo"]}
        overrides: dict[str, JsonValue] = {"plugin": [["local", "foo"]]}
        # When merged.
        merged = merge_config(base, overrides)
        # Then both exact forms are preserved (no cross-form collapsing).
        assert merged == {"plugin": ["foo", ["local", "foo"]]}

    def test_plugin_dict_entries_with_same_id_are_deduped(self) -> None:
        # Given plugin object entries sharing an id.
        base: dict[str, JsonValue] = {"plugin": [{"id": "foo"}]}
        overrides: dict[str, JsonValue] = {
            "plugin": [{"id": "foo"}, {"id": "bar"}]
        }
        # When merged.
        merged = merge_config(base, overrides)
        # Then the duplicate id collapses, order preserved.
        assert merged == {"plugin": [{"id": "foo"}, {"id": "bar"}]}

    def test_nested_plugin_under_deep_object_dedupes(self) -> None:
        # Given plugin arrays nested under a merged object.
        base: dict[str, JsonValue] = {"x": {"plugin": ["a"]}}
        overrides: dict[str, JsonValue] = {"x": {"plugin": ["a", "b"]}}
        # When merged.
        merged = merge_config(base, overrides)
        # Then the plugin policy applies at any depth.
        assert merged == {"x": {"plugin": ["a", "b"]}}


class TestMergeDeterminismAndSafety:
    def test_plugin_merge_is_idempotent(self) -> None:
        # Given a merged plugin result.
        base: dict[str, JsonValue] = {"plugin": ["foo", "bar"]}
        overrides: dict[str, JsonValue] = {"plugin": ["bar", "baz"]}
        once = merge_config(base, overrides)
        # When merged again against itself.
        again: dict[str, JsonValue] = {"plugin": ["bar", "baz"]}
        twice = merge_config(once, again)
        # Then the result is stable.
        assert twice == once

    def test_merge_does_not_mutate_inputs(self) -> None:
        # Given two configs.
        base: dict[str, JsonValue] = {"a": {"b": 1}, "plugin": ["foo"]}
        overrides: dict[str, JsonValue] = {"a": {"c": 2}, "plugin": ["bar"]}
        base_snapshot = copy.deepcopy(base)
        overrides_snapshot = copy.deepcopy(overrides)
        # When merged.
        _ = merge_config(base, overrides)
        # Then neither input was mutated.
        assert base == base_snapshot
        assert overrides == overrides_snapshot

    def test_merge_is_deterministic_for_same_inputs(self) -> None:
        base: dict[str, JsonValue] = {
            "a": {"b": 1},
            "plugin": ["foo", ["local", "x"]],
        }
        overrides: dict[str, JsonValue] = {
            "a": {"b": 2},
            "plugin": [["local", "x"], "bar"],
        }
        first = merge_config(base, overrides)
        second = merge_config(base, overrides)
        assert first == second
