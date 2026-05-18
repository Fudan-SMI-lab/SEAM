"""Tests for VariableResolver."""
import pytest
from core.variable_resolver import VariableResolver


@pytest.fixture
def resolver():
    return VariableResolver()


class TestBasicResolution:
    def test_literal_string_unchanged(self, resolver):
        assert resolver.resolve("hello") == "hello"

    def test_int_parse(self, resolver):
        assert resolver.resolve("123") == 123

    def test_float_parse(self, resolver):
        assert resolver.resolve("3.14") == 3.14

    def test_bool_parse(self, resolver):
        assert resolver.resolve("true") is True
        assert resolver.resolve("false") is False

    def test_state_nested_path(self, resolver):
        result = resolver.resolve("${state.a.b.c}", state={"a": {"b": {"c": "deep"}}})
        assert result == "deep"

    def test_globals_key(self, resolver):
        result = resolver.resolve("${globals.max}", globals={"max": 5})
        assert result == 5

    def test_context_key(self, resolver):
        result = resolver.resolve("${context.PROJECT_DIR}", context={"PROJECT_DIR": "/tmp/test"})
        assert result == "/tmp/test"

    def test_loop_vars(self, resolver):
        result = resolver.resolve("${loop_vars.entry_script}", loop_vars={"entry_script": "python main.py"})
        assert result == "python main.py"

    def test_loop_state(self, resolver):
        result = resolver.resolve("${loop_state.exit_code}", loop_state={"exit_code": 1})
        assert result == 1

    def test_loop_history(self, resolver):
        history = [{"iter": 1}, {"iter": 2}]
        result = resolver.resolve("${loop_history}", loop_history=history)
        assert result == history

    def test_step_outputs(self, resolver):
        result = resolver.resolve("${error_analysis.repair_role}", step_outputs={"error_analysis": {"repair_role": "code_adapter"}})
        assert result == "code_adapter"


class TestDefaultFilter:
    def test_default_when_missing(self, resolver):
        result = resolver.resolve("${missing_var | default 99}")
        assert result == 99

    def test_default_when_present(self, resolver):
        result = resolver.resolve("${context.X | default fallback}", context={"X": "present"})
        assert result == "present"

    def test_default_string_value(self, resolver):
        result = resolver.resolve("${missing | default 'hello'}")
        assert result == "hello"

    def test_default_bool_value(self, resolver):
        result = resolver.resolve("${missing | default true}")
        assert result is True


class TestStateWildcard:
    def test_state_wildcard_returns_all(self, resolver):
        result = resolver.resolve("${state.*}", state={"phase_0": {"a": 1}, "phase_1": {"b": 2}})
        assert "phase_0" in result
        assert "phase_1" in result
        assert result["phase_0"]["a"] == 1

    def test_state_wildcard_empty(self, resolver):
        result = resolver.resolve("${state.*}", state={})
        assert isinstance(result, dict)
        assert len(result) == 0


class TestResolveDict:
    def test_resolve_dict_nested(self, resolver):
        data = {"key": "${context.X}", "nested": {"val": "${globals.Y}"}}
        result = resolver.resolve_dict(data, context={"X": "x_val"}, globals={"Y": "y_val"})
        assert result["key"] == "x_val"
        assert result["nested"]["val"] == "y_val"

    def test_resolve_dict_list(self, resolver):
        data = ["${context.A}", "literal", 42]
        result = resolver.resolve_dict(data, context={"A": "found"})
        assert result[0] == "found"
        assert result[1] == "literal"
        assert result[2] == 42

    def test_resolve_dict_preserves_non_string(self, resolver):
        data = {"count": 42, "active": True}
        result = resolver.resolve_dict(data)
        assert result == data

    def test_resolve_dict_none_value(self, resolver):
        data = {"val": None}
        result = resolver.resolve_dict(data)
        assert result["val"] is None


class TestEdgeCases:
    def test_unknown_variable_returns_none(self, resolver):
        result = resolver.resolve("${unknown.path}")
        assert result is None

    def test_multiple_templates_in_string(self, resolver):
        result = resolver.resolve("${context.A}/${context.B}", context={"A": "part1", "B": "part2"})
        assert result == "part1/part2"

    def test_template_mixed_with_literal(self, resolver):
        result = resolver.resolve("run ${context.CMD} now", context={"CMD": "test"})
        assert result == "run test now"
