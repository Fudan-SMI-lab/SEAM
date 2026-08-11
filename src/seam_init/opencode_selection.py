"""Interactive provider/model selection and API-key collection.

Provides the override builder for custom OpenAI-compatible providers
(exact camelCase ``baseURL``/``apiKey``), structural merge application
(:func:`core.jsonc.merge_config`), interactive provider/model selection
through :class:`PromptPort`, and the API-key flow with explicit
plaintext-storage-risk confirmation before calling ``secret()``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, final, runtime_checkable

from core.jsonc import JsonValue, merge_config
from seam_init.models import AuthState, ModelId, ProviderId, ProviderSelection

__all__ = [
    "CustomProviderSpec", "PromptPort", "apply_selection",
    "build_custom_provider_override", "collect_api_key",
    "define_custom_provider", "provider_has_api_key", "select_provider_model",
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
    if not prompt.confirm(_PlainTextRiskPrompt, default=False):
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
    """Interactively select a provider/model or define a custom provider.

    Shows discovered providers and available models as hints. Returns
    ``(selection, custom_or_None)`` or ``None`` if the user cancels.
    """
    providers_raw = discovered_value.get("provider")
    known: list[str] = (
        [str(k) for k in providers_raw] if isinstance(providers_raw, dict) else []
    )
    hint = ", ".join(known) if known else "(none discovered)"
    models_hint = str(len(available_models)) if available_models else "0"
    full_prompt = f"{_SelectProviderPrompt} Discovered: {hint}; models: {models_hint}"
    choice = prompt.ask(full_prompt).strip().lower()
    if not choice:
        return None
    if choice == "custom":
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
    provider_id = choice
    model_id = prompt.ask(
        _SelectModelPrompt.format(provider=provider_id),
    ).strip()
    if not provider_id or not model_id:
        return None
    selection = ProviderSelection(
        provider_id=ProviderId(provider_id),
        model_id=ModelId(model_id),
    )
    return selection, None
