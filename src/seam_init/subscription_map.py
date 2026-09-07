"""Deterministic provider-family to OMO subscription flag mapping.

An explicit alias table maps a normalized provider id to exactly one provider
family; custom/unknown ids yield the all-default selection (every flag
``False``/``TriState.NO``). Alias sets are disjoint by construction, so the
mapping is order-independent and total.
"""
from __future__ import annotations

from typing import Final, final

from seam_init.omo_install import SubscriptionSelection, TriState

__all__ = ["FamilySubscriptionSelector", "select_subscription"]

_OPENAI: Final[frozenset[str]] = frozenset({"openai"})
_ANTHROPIC: Final[frozenset[str]] = frozenset({"anthropic", "claude"})
_GEMINI: Final[frozenset[str]] = frozenset({"gemini", "google"})
_COPILOT: Final[frozenset[str]] = frozenset({"copilot", "github-copilot", "github"})
_OPENCODE_ZEN: Final[frozenset[str]] = frozenset({"opencode-zen", "opencode_zen", "zen"})
_OPENCODE_GO: Final[frozenset[str]] = frozenset({"opencode-go", "opencode_go"})
_ZAI: Final[frozenset[str]] = frozenset({"zai", "zai-coding-plan", "zai_coding_plan"})
_KIMI: Final[frozenset[str]] = frozenset(
    {"kimi", "kimi-for-coding", "kimi_for_coding", "moonshot"})
_BAILIAN: Final[frozenset[str]] = frozenset(
    {"bailian", "bailian-coding-plan", "bailian_coding_plan", "alibaba", "dashscope"})
_MINIMAX_CN: Final[frozenset[str]] = frozenset(
    {"minimax-cn", "minimax_cn", "minimax-cn-coding-plan"})
_MINIMAX: Final[frozenset[str]] = frozenset(
    {"minimax", "minimax-coding-plan", "minimax_coding_plan"})
_VERCEL: Final[frozenset[str]] = frozenset(
    {"vercel", "vercel-ai-gateway", "vercel_ai_gateway"})


def select_subscription(provider_id: str) -> SubscriptionSelection:
    """Map a provider id to its OMO subscription flags; unknown → all defaults."""
    pid = provider_id.strip().lower()
    return SubscriptionSelection(
        claude=TriState.YES if pid in _ANTHROPIC else TriState.NO,
        openai=pid in _OPENAI,
        gemini=pid in _GEMINI,
        copilot=pid in _COPILOT,
        opencode_zen=pid in _OPENCODE_ZEN,
        zai_coding_plan=pid in _ZAI,
        opencode_go=pid in _OPENCODE_GO,
        kimi_for_coding=pid in _KIMI,
        bailian_coding_plan=pid in _BAILIAN,
        minimax_cn_coding_plan=pid in _MINIMAX_CN,
        minimax_coding_plan=pid in _MINIMAX,
        vercel_ai_gateway=pid in _VERCEL,
    )


@final
class FamilySubscriptionSelector:
    """SubscriptionSelector port backed by :func:`select_subscription`."""

    def select(self, provider_id: str) -> SubscriptionSelection:
        return select_subscription(provider_id)
