"""Variable resolver for ${...} template syntax parsing."""

import re
from typing import Any


class VariableResolver:
    """Resolve ${path.to.field} templates against state/globals/context dicts."""

    def __init__(self):
        self._pattern = re.compile(r"\$\{([^}]+)\}")

    def resolve(  # pylint: disable=too-many-arguments; silent
        self,
        template: Any,
        *,
        state: dict | None = None,
        globals: dict | None = None,  # pylint: disable=redefined-builtin; silent
        context: dict | None = None,
        loop_vars: dict | None = None,
        loop_state: dict | None = None,
        loop_history: list | None = None,
        step_outputs: dict | None = None,
    ) -> Any:
        """Resolve ${} templates in a value.

        Returns the original value unchanged if it doesn't contain ${}.
        For literal int/bool strings without ${}, parses and returns native type.
        """
        if not isinstance(template, str):
            return template

        if "${" not in template:
            return self._parse_literal(template)

        def replacer(match: re.Match) -> str:
            expr = match.group(1).strip()
            return str(
                self._resolve_expr(
                    expr,
                    state=state,
                    globals=globals,
                    context=context,
                    loop_vars=loop_vars,
                    loop_state=loop_state,
                    loop_history=loop_history,
                    step_outputs=step_outputs,
                )
            )

        result = self._pattern.sub(replacer, template)

        # If the template was exactly one ${...} expression, return the raw value
        m = self._pattern.fullmatch(template)
        if m is not None:
            expr = m.group(1).strip()
            val = self._resolve_expr(
                expr,
                state=state,
                globals=globals,
                context=context,
                loop_vars=loop_vars,
                loop_state=loop_state,
                loop_history=loop_history,
                step_outputs=step_outputs,
            )
            return val

        return result

    def resolve_dict(
        self,
        data: dict | list | str | int | bool,
        **scopes: dict,
    ) -> dict | list | str | int | bool:
        """Recursively resolve ${} templates in a nested dict/list structure."""
        if isinstance(data, dict):
            return {k: self.resolve_dict(v, **scopes) for k, v in data.items()}
        if isinstance(data, list):
            return [self.resolve_dict(item, **scopes) for item in data]
        if isinstance(data, str):
            if "${" in data:
                return self.resolve(data, **scopes)
            return data
        return data

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _parse_literal(self, value: str) -> Any:
        """Parse bare string literals (bool/int/float) or return as-is."""
        if isinstance(value, str):
            lower = value.lower()
            if lower == "true":
                return True
            if lower == "false":
                return False
            # Try int
            try:
                return int(value)
            except ValueError:
                pass
            # Try float
            try:
                return float(value)
            except ValueError:
                pass
        return value

    def _resolve_expr(  # pylint: disable=too-many-arguments; silent
        self,
        expr: str,
        *,
        state: dict | None = None,
        globals: dict | None = None,  # pylint: disable=redefined-builtin; silent
        context: dict | None = None,
        loop_vars: dict | None = None,
        loop_state: dict | None = None,
        loop_history: list | None = None,
        step_outputs: dict | None = None,
    ) -> Any:
        """Resolve a single expression (may contain | default filter)."""
        # Split on | default
        default_value = None
        parts = re.split(r"\|\s*default\s+", expr, maxsplit=1)
        if len(parts) == 2:
            expr = parts[0].strip()
            default_val_str = parts[1].strip()
            default_value = self._parse_default_value(default_val_str)

        result = self._lookup(
            expr,
            state=state,
            globals=globals,
            context=context,
            loop_vars=loop_vars,
            loop_state=loop_state,
            loop_history=loop_history,
            step_outputs=step_outputs,
        )

        if result is None and default_value is not None:
            return default_value
        return result

    # pylint: disable-next=too-many-return-statements; silent
    def _parse_default_value(self, text: str) -> Any:
        """Parse a default value string (supports quoted strings, numbers, bools)."""
        text = text.strip()
        # Quoted string
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            return text[1:-1]
        # Boolean
        if text.lower() == "true":
            return True
        if text.lower() == "false":
            return False
        # None
        if text.lower() in ("none", "null"):
            return None
        # Int
        try:
            return int(text)
        except ValueError:
            pass
        # Float
        try:
            return float(text)
        except ValueError:
            pass
        return text

    # pylint: disable-next=too-many-arguments,too-many-branches,too-many-return-statements; silent
    def _lookup(
        self,
        expr: str,
        *,
        state: dict | None = None,
        globals: dict | None = None,  # pylint: disable=redefined-builtin; silent
        context: dict | None = None,
        loop_vars: dict | None = None,
        loop_state: dict | None = None,
        loop_history: list | None = None,
        step_outputs: dict | None = None,
    ) -> Any:
        """Look up expr in scopes by priority order."""
        parts = expr.split(".")

        # 1. ${state.*} — return ALL entries
        if expr == "state.*":
            return dict(state) if state else {}

        # 2. ${state.phase_id} — return complete output dict
        # 3. ${state.phase_id.field_name} — nested access
        if parts[0] == "state" and state:
            phase_id = parts[1]
            if len(parts) == 2:
                return state.get(phase_id)
            # Nested: state.phase.field → state[phase][field]
            val = state.get(phase_id)
            if val and isinstance(val, dict):
                return self._nested_lookup(val, parts[2:])
            return None

        # 4. ${globals.key}
        if parts[0] == "globals" and globals:
            val = globals.get(parts[1])
            if len(parts) > 2 and isinstance(val, dict):
                return self._nested_lookup(val, parts[2:])
            return val

        # 5. ${context.KEY}
        if parts[0] == "context" and context:
            val = context.get(parts[1])
            if len(parts) > 2 and isinstance(val, dict):
                return self._nested_lookup(val, parts[2:])
            return val

        # 6. ${loop_vars.key}
        if parts[0] == "loop_vars" and loop_vars:
            val = loop_vars.get(parts[1])
            if len(parts) > 2 and isinstance(val, dict):
                return self._nested_lookup(val, parts[2:])
            return val

        # 7. ${loop_state.key}
        if parts[0] == "loop_state" and loop_state:
            val = loop_state.get(parts[1])
            if len(parts) > 2 and isinstance(val, dict):
                return self._nested_lookup(val, parts[2:])
            return val

        # 8. ${loop_history}
        if expr == "loop_history":
            return loop_history

        # 9. ${step_name.field} — intra-loop step refs
        if step_outputs and len(parts) >= 2:
            step_name = parts[0]
            if step_name in step_outputs:
                val = step_outputs[step_name]
                if len(parts) >= 2:
                    if isinstance(val, dict):
                        return self._nested_lookup(val, parts[1:])
                return val

        # 9b. Bare name — try state first (for outputs stored as state[key])
        if state and len(parts) >= 1:
            top = parts[0]
            if top in state:
                val = state[top]
                if len(parts) > 1 and isinstance(val, dict):
                    return self._nested_lookup(val, parts[1:])
                return val if len(parts) == 1 else None

        # 10. $ prefix without known scope — try step_outputs, globals, context
        if step_outputs and expr in step_outputs:
            return step_outputs[expr]
        if globals and expr in globals:
            return globals[expr]
        if context and expr in context:
            return context[expr]

        # 11. No match
        return None

    def _nested_lookup(self, obj: dict, keys: list[str]) -> Any:
        """Drill into nested dict with a list of keys."""
        current = obj
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
                if current is None:
                    return None
            else:
                return None
        return current
