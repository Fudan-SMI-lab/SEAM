"""Interactive provider/model selection and API-key collection.

Provides the override builder for custom OpenAI-compatible providers
(exact camelCase ``baseURL``/``apiKey``), structural merge application
(:func:`core.jsonc.merge_config`), interactive provider/model selection
through :class:`PromptPort`, the API-key flow with explicit
plaintext-storage-risk confirmation before calling ``secret()``, the
secret-free :class:`ConfigProjection` of what a merged candidate writes
(used by the post-write runtime proof in ``opencode_discovery``), and a
built-in provider preset catalog loaded from ``data/provider_presets.json``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final, runtime_checkable

from core.jsonc import JsonValue, merge_config
from seam_init.models import AuthState, ModelId, ProviderId, ProviderSelection

__all__ = [
    "ConfigProjection", "CustomProviderSpec", "ModelPreset", "PromptPort",
    "ProviderPreset", "ProviderProjection",
    "apply_selection", "build_config_projection", "build_custom_provider_override",
    "collect_api_key", "define_custom_provider", "load_provider_presets",
    "provider_has_api_key", "select_provider_model",
]

_OPENAI_COMPATIBLE_NPM: Final[str] = "@ai-sdk/openai-compatible"
_PlainTextRiskPrompt: Final[str] = (
    "The API key will be stored in plaintext in the OpenCode config file "
    "(owner-only at 0600). Continue?"
)
_CustomPrompt: Final[str] = (
    "Enter a custom OpenAI-compatible provider id (e.g., my-proxy):"
)
_BaseUrlPrompt: Final[str] = "Enter the provider base URL (e.g., https://api.example.com/v1):"
_ModelIdPrompt: Final[str] = "Enter the model id (e.g., gpt-4o):"
_ModelNamePrompt: Final[str] = "Enter the display name for this model:"
_SelectProviderPrompt: Final[str] = (
    "Select a provider by id, or type 'custom' to define a new "
    "OpenAI-compatible provider:"
)
_SelectModelPrompt: Final[str] = "Select a model id from provider '{provider}':"


@runtime_checkable
class PromptPort(Protocol):
    """Subset of seam_init.cli.PromptPort used by configuration."""

    def ask(self, prompt: str, *, default: str | None = None) -> str: ...

    def secret(self, prompt: str) -> str: ...

    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...


@final
@dataclass(frozen=True, slots=True)
class CustomProviderSpec:
    """User-defined OpenAI-compatible provider definition."""

    provider_id: str
    base_url: str
    model_id: str
    model_name: str


@final
@dataclass(frozen=True, slots=True)
class ModelPreset:
    """One model option in a provider preset."""

    id: str
    name: str


@final
@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """A built-in provider option with known configuration."""

    id: str
    name: str
    type: str  # "native" or "openai-compatible"
    base_url: str | None
    needs_api_key: bool
    models: tuple[ModelPreset, ...]


_PRESET_TYPE_NATIVE: Final[str] = "native"
_PRESET_TYPE_COMPAT: Final[str] = "openai-compatible"


def load_provider_presets() -> tuple[ProviderPreset, ...]:
    """Load the provider preset catalog from the bundled JSON data file."""
    preset_path = Path(__file__).parent / "data" / "provider_presets.json"
    raw = json.loads(preset_path.read_text(encoding="utf-8"))
    presets: list[ProviderPreset] = []
    for item in raw.get("presets", []):
        models = tuple(
            ModelPreset(id=str(m["id"]), name=str(m["name"]))
            for m in item.get("models", [])
        )
        presets.append(ProviderPreset(
            id=str(item["id"]),
            name=str(item["name"]),
            type=str(item.get("type", _PRESET_TYPE_COMPAT)),
            base_url=item.get("base_url"),
            needs_api_key=bool(item.get("needs_api_key", True)),
            models=models,
        ))
    return tuple(presets)


@final
@dataclass(frozen=True, slots=True)
class ProviderProjection:
    """Secret-free structural projection of the selected provider entry."""

    provider_id: str
    npm: str | None
    base_url: str | None
    model_ids: tuple[str, ...]
    has_api_key: bool


@final
@dataclass(frozen=True, slots=True)
class ConfigProjection:
    """Secret-free semantic projection of the merged fresh candidate."""

    model_ref: str
    provider: ProviderProjection | None


def build_config_projection(
    merged: Mapping[str, JsonValue], provider_id: str,
) -> ConfigProjection:
    """Project the secret-free structure a fresh candidate writes.

    Only non-secret structure is captured: npm adapter, baseURL endpoint,
    model keys. An apiKey is reduced to a presence flag; its value is never
    read into the projection, facts, output, or failure details.
    """
    model = merged.get("model")
    providers = merged.get("provider")
    entry = providers.get(provider_id) if isinstance(providers, dict) else None
    if not isinstance(entry, dict):
        return ConfigProjection(
            model_ref=model if isinstance(model, str) else "", provider=None)
    npm = entry.get("npm")
    raw_models = entry.get("models")
    raw_options = entry.get("options")
    options = raw_options if isinstance(raw_options, dict) else {}
    url = options.get("baseURL")
    key = options.get("apiKey")
    return ConfigProjection(
        model_ref=model if isinstance(model, str) else "",
        provider=ProviderProjection(
            provider_id, npm if isinstance(npm, str) else None,
            url if isinstance(url, str) else None,
            tuple(str(k) for k in raw_models) if isinstance(raw_models, dict) else (),
            isinstance(key, str) and bool(key.strip())))


def build_custom_provider_override(
    spec: CustomProviderSpec, api_key: str | None,
) -> dict[str, JsonValue]:
    """Build the exact camelCase override for an OpenAI-compatible provider.

    ``api_key`` (when truthy) lands ONLY at ``provider.<id>.options.apiKey``.
    Top-level ``model`` is set to ``"<provider_id>/<model_id>"``.
    """
    options: dict[str, JsonValue] = {"baseURL": spec.base_url}
    if api_key:
        options["apiKey"] = api_key
    provider_entry: dict[str, JsonValue] = {
        "npm": _OPENAI_COMPATIBLE_NPM,
        "name": spec.model_name,
        "options": options,
        "models": {spec.model_id: {"name": spec.model_name}},
    }
    return {
        "provider": {spec.provider_id: provider_entry},
        "model": f"{spec.provider_id}/{spec.model_id}",
    }


def apply_selection(
    base: Mapping[str, JsonValue],
    selection: ProviderSelection,
    api_key: str | None,
    custom: CustomProviderSpec | None,
) -> dict[str, JsonValue]:
    """Structurally merge the selection's override onto base; idempotent."""
    if custom is not None:
        override = build_custom_provider_override(custom, api_key)
    elif api_key:
        override = {
            "provider": {
                str(selection.provider_id): {"options": {"apiKey": api_key}},
            },
            "model": f"{selection.provider_id}/{selection.model_id}",
        }
    else:
        override: dict[str, JsonValue] = {
            "model": f"{selection.provider_id}/{selection.model_id}",
        }
    return merge_config(base, override)


def provider_has_api_key(
    base: Mapping[str, JsonValue], provider_id: ProviderId,
) -> bool:
    """Return whether the selected native provider already has a nonempty key."""
    providers = base.get("provider")
    if not isinstance(providers, dict):
        return False
    provider = providers.get(str(provider_id))
    if not isinstance(provider, dict):
        return False
    options = provider.get("options")
    if not isinstance(options, dict):
        return False
    key = options.get("apiKey")
    return isinstance(key, str) and bool(key.strip())


def collect_api_key(prompt: PromptPort) -> str | None:
    """Confirm plaintext-risk, then collect key via secret(); None if declined.

    If the user declines the plaintext-risk confirmation or enters an empty
    key, returns None (caller records AUTH_SKIPPED). Never places the key in
    argv, stdout, or any non-secret channel.
    """
    if not prompt.confirm(_PlainTextRiskPrompt, default=True):
        return None
    key = prompt.secret("API key")
    return key if key.strip() else None


def define_custom_provider(prompt: PromptPort) -> CustomProviderSpec | None:
    """Interactively collect a custom OpenAI-compatible provider definition."""
    provider_id = prompt.ask(_CustomPrompt).strip()
    if not provider_id:
        return None
    base_url = prompt.ask(_BaseUrlPrompt).strip()
    if not base_url:
        return None
    model_id = prompt.ask(_ModelIdPrompt).strip()
    if not model_id:
        return None
    model_name = prompt.ask(_ModelNamePrompt, default=model_id).strip()
    return CustomProviderSpec(
        provider_id=provider_id,
        base_url=base_url,
        model_id=model_id,
        model_name=model_name or model_id,
    )


def select_provider_model(
    prompt: PromptPort,
    discovered_value: Mapping[str, JsonValue],
    available_models: tuple[str, ...],
) -> tuple[ProviderSelection, CustomProviderSpec | None] | None:
    """Interactively select a provider/model from presets, existing config, or custom.

    Shows a numbered preset menu (loaded from ``data/provider_presets.json``),
    a custom-entry option, and discovered providers as hints. Returns
    ``(selection, custom_or_None)`` or ``None`` if the user cancels.
    """
    presets = load_provider_presets()
    _ = available_models

    lines: list[str] = ["Available providers:"]
    for i, p in enumerate(presets, 1):
        model_hint = ", ".join(m.id for m in p.models) if p.models else "(specify manually)"
        lines.append(f"  [{i}] {p.name} — {model_hint}")
    custom_num = len(presets) + 1
    lines.append(f"  [{custom_num}] Custom (enter manually)")
    menu = "\n".join(lines)

    choice = prompt.ask(f"{menu}\nSelect provider [1-{custom_num}]").strip()
    if not choice:
        return None
    try:
        idx = int(choice) - 1
    except ValueError:
        return None
    if idx == len(presets):
        custom = define_custom_provider(prompt)
        if custom is None:
            return None
        selection = ProviderSelection(
            provider_id=ProviderId(custom.provider_id),
            model_id=ModelId(custom.model_id),
            base_url=custom.base_url,
            auth_state=AuthState.SKIPPED,
        )
        return selection, custom
    if idx < 0 or idx >= len(presets):
        return None
    return _select_from_preset(prompt, presets[idx])


def _select_from_preset(
    prompt: PromptPort, preset: ProviderPreset,
) -> tuple[ProviderSelection, CustomProviderSpec | None] | None:
    """Select a model from a preset; build the right selection/custom pair."""
    lines = [f"Available models for {preset.name}:"]
    for i, m in enumerate(preset.models, 1):
        lines.append(f"  [{i}] {m.id} ({m.name})")
    manual_num = len(preset.models) + 1
    lines.append(f"  [{manual_num}] Enter model id manually")
    menu = "\n".join(lines)

    choice = prompt.ask(f"{menu}\nSelect model [1-{manual_num}]").strip()
    if not choice:
        return None
    try:
        midx = int(choice) - 1
    except ValueError:
        return None
    if 0 <= midx < len(preset.models):
        model_id = preset.models[midx].id
        model_name = preset.models[midx].name
    elif midx == len(preset.models):
        model_id = prompt.ask("Enter model id:").strip()
        if not model_id:
            return None
        model_name = model_id
    else:
        return None

    if preset.type == _PRESET_TYPE_NATIVE:
        selection = ProviderSelection(
            provider_id=ProviderId(preset.id),
            model_id=ModelId(model_id),
        )
        return selection, None

    default_url = preset.base_url or ""
    raw = prompt.ask(
        f"Provider base URL [{default_url}]:", default=default_url).strip()
    base_url = raw if raw else default_url
    custom = CustomProviderSpec(
        provider_id=preset.id,
        base_url=base_url,
        model_id=model_id,
        model_name=model_name,
    )
    selection = ProviderSelection(
        provider_id=ProviderId(preset.id),
        model_id=ModelId(model_id),
        base_url=base_url,
        auth_state=AuthState.SKIPPED,
    )
    return selection, custom
