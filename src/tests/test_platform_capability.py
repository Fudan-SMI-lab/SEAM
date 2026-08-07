"""RED tests for #8 platform-capability classification infrastructure (T5).

Semantics (Metis Q2 restatement): this file locks the *classification
infrastructure* that bug #8 requires — a platform-capability precheck, an
``unsupported_backend`` classification, a platform-policy satisfaction
predicate, and an explicit "degraded/降级" annotation for CPU fallback. It
does NOT claim #8's root cause is fixed, and it does NOT test real-hardware
behavior (real-hardware verification = 待硬件环境, see
``.omo/evidence/platform-verification-registry.md``).

IMPORTANT premise correction (T1): ``_ACCELERATOR_PREFIXES`` in
``core/accelerator_context.py`` ALREADY contains ``torch_npu``/``torch_npu_``
at L19-20 at HEAD (6018b85). The plan's "无 npu" premise was WRONG. These RED
tests therefore target the MISSING capability-precheck function and the
classification infrastructure, NOT npu-prefix recognition. The npu recognition
test at the bottom is a CONTROL that must PASS on current code.

Intended API surface — T8 must implement exactly these names so this RED
file turns GREEN with the new infrastructure:

* ``accelerator_context.get_platform_capabilities(installed_packages) -> dict[str, bool]``
  Per-family capability booleans, e.g. ``{"npu": bool, "ppu": bool,
  "cuda": bool, "cpu": bool, ...}``. Does NOT exist at HEAD.

* ``accelerator_context.precheck_platform_capability(installed_packages,
  target_family: str) -> dict``
  Capability classifier. Returns a dict shaped like the existing
  ``blocked_reason: "unsupported_backend"`` pattern in
  workflow_executor.py L5444-5450::

      {
          "supported": bool,
          "blocked_reason": "unsupported_backend" | None,
          "usable_backend": str | None,
          "degraded_fallback": bool,
          "platform_degraded": bool,   # explicit degraded/降级 marker (CPU fallback)
      }

  Does NOT exist at HEAD.

* ``platform_policy.satisfies_platform_requirements(policy, evidence: dict) -> bool``
  #17's ``platform_policy_satisfied`` dependency. At HEAD only token helpers
  exist (platform_policy.py L883-971); no satisfaction predicate.
"""

from core import accelerator_context
from core import platform_policy


class TestPlatformCapabilityPrecheck:
    """(a) A platform-capability precheck function must exist and return a dict.

    RED for the new infrastructure: no capability precheck exists at HEAD,
    so these tests fail with a plain AssertionError (missing feature).
    """

    def test_get_platform_capabilities_function_exists(self):
        """RED: get_platform_capabilities() is absent at HEAD — hasattr assertion.

        T8 must add ``accelerator_context.get_platform_capabilities(
        installed_packages) -> dict[str, bool]`` returning per-family
        capability booleans.
        """
        assert hasattr(
            accelerator_context, "get_platform_capabilities"
        ), (
            "accelerator_context.get_platform_capabilities() is missing — "
            "#8 capability precheck infrastructure not implemented"
        )

    def test_get_platform_capabilities_returns_per_family_dict(self):
        """RED: calling the (missing) function must return a per-family bool dict."""
        capabilities = accelerator_context.get_platform_capabilities(
            ["torch_npu==2.1.0", "torch==2.0.1"]
        )
        assert isinstance(capabilities, dict)
        assert "npu" in capabilities
        assert isinstance(capabilities["npu"], bool)


class TestUnsupportedBackendClassification:
    """(b) ``blocked_reason: "unsupported_backend"`` must be triggerable via the
    capability precheck (modeled on workflow_executor.py L5444-5450)."""

    def test_unsupported_backend_classification_triggerable(self):
        """RED: no precheck classifier exists at HEAD.

        Scenario: gguf-style model backend (llama-cpp-python, CUDA-bound) on an
        npu target where no usable npu backend is available. The precheck must
        classify this as ``blocked_reason: "unsupported_backend"`` instead of
        silently proceeding on CPU.
        """
        assert hasattr(
            accelerator_context, "precheck_platform_capability"
        ), (
            "accelerator_context.precheck_platform_capability() is missing — "
            "no unsupported_backend classifier for #8"
        )
        installed = [
            "torch_npu==2.1.0",  # npu family present
            "llama-cpp-python==0.2.90",  # gguf-style CUDA backend, not npu
        ]
        classification = accelerator_context.precheck_platform_capability(
            installed, target_family="npu"
        )
        assert classification.get("blocked_reason") == "unsupported_backend"


class TestPlatformPolicySatisfaction:
    """(c) A platform-policy satisfaction predicate must exist (#17
    ``platform_policy_satisfied`` dependency). At HEAD platform_policy.py L238
    only holds native-build-log tokens; there is no satisfaction check."""

    def test_satisfies_platform_requirements_function_exists(self):
        """RED: satisfies_platform_requirements() is absent at HEAD — hasattr assertion."""
        assert hasattr(
            platform_policy, "satisfies_platform_requirements"
        ), (
            "platform_policy.satisfies_platform_requirements() is missing — "
            "#17 platform_policy_satisfied depends on it"
        )

    def test_satisfies_platform_requirements_returns_bool(self):
        """RED: the (missing) predicate must return bool for policy + evidence."""
        policy = platform_policy.BUILTIN_PRESETS["npu_ascend"]
        evidence = {"target_device": "npu"}
        satisfied = platform_policy.satisfies_platform_requirements(policy, evidence)
        assert isinstance(satisfied, bool)


class TestCpuFallbackDegradationAnnotation:
    """(d) CPU fallback must produce an explicit "degraded/降级" annotation rather
    than silent success (bug #8/#9: CPU fallback must not be reported as success)."""

    def test_cpu_fallback_annotated_as_degraded(self):
        """RED: no degradation annotation exists at HEAD.

        T8: when the target accelerator family has no usable backend but CPU is
        available, ``precheck_platform_capability`` must carry an explicit
        ``platform_degraded: true`` marker (a reportable 降级 outcome) instead of
        silently succeeding on CPU.
        """
        installed = ["torch==2.0.1", "numpy"]  # CPU-only environment
        classification = accelerator_context.precheck_platform_capability(
            installed, target_family="npu"
        )
        assert classification.get("platform_degraded") is True
        assert classification.get("degraded_fallback") is True


class TestNpuRecognitionControl:
    """CONTROL — must PASS on current code (T1 premise correction).

    DO NOT RED-TEST npu recognition: ``_ACCELERATOR_PREFIXES`` ALREADY has
    ``torch_npu``/``torch_npu_`` at accelerator_context.py L19-20 at HEAD.
    This control asserts the positive premise so the plan's false "无 npu"
    assumption is not re-introduced. Only maca/musa/muxi/metax are missing
    (that is #9/T9's scope — NOT touched here).
    """

    def test_npu_family_is_recognized_control(self):
        from core.accelerator_context import extract_accelerator_context

        result = extract_accelerator_context(["torch_npu==2.1.0"])
        assert "torch_npu" in result["accelerator_packages"]
        assert result["torch_npu_version"] == "2.1.0"


# ---------------------------------------------------------------------------
# T9 RED — #9 MUSA/MACA/Metax capability-recognition gap (RED track, no impl)
# ---------------------------------------------------------------------------
# #9 original bug: a GGUF model (llama-cpp-python / gguf / ctransformers, all
# CUDA-bound) on a MUSA/MACA environment WITHOUT a matching native backend must
# be classified ``blocked_reason: "unsupported_backend"`` — never a silent CPU
# fallback. T8 deliberately scoped MUSA/MACA/Metax OUT (accelerator_context.py
# L138-139: "_FAMILY_PREFIXES currently has npu/ppu/cuda/cpu only — MUSA/MACA/
# Metax prefixes are #9/T9's scope and are intentionally NOT added"; L162-163:
# _CAPABILITY_FAMILIES = ("npu","ppu","cuda","cpu"); L17-41 _ACCELERATOR_PREFIXES
# has no musa/maca/muxi/metax). These tests lock the #9 gap; T11 implements the
# prefixes + family recognition. They must FAIL on current code (e3d003d) for
# the correct reason — missing prefix / missing recognition / missing
# classification — and turn GREEN only with T11's implementation.


class TestMusaMacaMetaxPrefixRecognition:
    """(a) #9: MUSA/MACA/Metax accelerator prefixes must be recognized.

    RED: ``_ACCELERATOR_PREFIXES`` has no ``musa``/``maca``/``muxi``/``metax``
    entries at HEAD (e3d003d), so this assertion fails with a plain
    AssertionError — missing feature, exactly the #9 gap.
    """

    def test_accelerator_prefixes_contain_musa_maca_muxi_metax(self):
        prefixes = set(accelerator_context._ACCELERATOR_PREFIXES)
        for family in ("musa", "maca", "muxi", "metax"):
            assert family in prefixes, (
                f"#9: accelerator_context._ACCELERATOR_PREFIXES is missing "
                f"{family!r} — MUSA/MACA/Metax prefix recognition not "
                f"implemented (T8 scope, T9/T11 gap)"
            )


class TestMusaCapabilityPrecheckSuccessPath:
    """(b) #9: a MUSA environment must trigger the capability-precheck success
    path — musa recognized as a capability family AND precheck supported=True."""

    def test_musa_env_yields_musa_capability_and_supported_precheck(self):
        installed = ["torch_musa==1.0.0", "torch==2.0.1"]  # MUSA env (torch-musa)
        capabilities = accelerator_context.get_platform_capabilities(installed)
        assert capabilities.get("musa") is True, (
            "#9: get_platform_capabilities() must report capabilities['musa'] "
            "is True for a torch_musa environment — musa family missing from "
            "_FAMILY_PREFIXES (got %r)" % capabilities
        )
        classification = accelerator_context.precheck_platform_capability(
            installed, target_family="musa"
        )
        assert classification["supported"] is True, (
            "#9: MUSA env targeting family 'musa' must precheck as "
            f"supported: True — got {classification}"
        )


class TestGgufMusaMacaUnsupportedBackend:
    """(c) #9 original bug: a GGUF-style CUDA-bound backend (llama-cpp-python)
    on a musa/maca environment with no matching native backend must classify as
    ``blocked_reason: "unsupported_backend"`` — never silent CPU success.

    NOTE: on current code ``precheck_platform_capability`` already returns
    ``unsupported_backend`` for target='musa'/'maca' — but only because the
    family is UNRECOGNIZED (target not in capabilities). The musa/maca
    recognition assertions below are therefore the true RED gate: they fail at
    HEAD for the correct reason (missing recognition) and, once T11 adds the
    families, the blocked_reason/supported assertions lock the #9 semantic so a
    wrong T11 (recognize-but-silently-CPU) cannot sneak through.
    """

    def test_gguf_backend_on_musa_classified_unsupported_backend(self):
        installed = [
            "torch_musa==1.0.0",  # musa family present
            "llama-cpp-python==0.2.90",  # gguf-style CUDA-bound backend
        ]
        capabilities = accelerator_context.get_platform_capabilities(installed)
        assert capabilities.get("musa") is True, (
            "#9: musa environment must be recognized before backend "
            "classification — musa family missing (got %r)" % capabilities
        )
        classification = accelerator_context.precheck_platform_capability(
            installed, target_family="musa"
        )
        assert classification["blocked_reason"] == "unsupported_backend", (
            "#9: GGUF model on musa without matching backend must classify "
            f"unsupported_backend — got {classification}"
        )
        assert classification["supported"] is False
        assert classification["degraded_fallback"] is False

    def test_gguf_backend_on_maca_classified_unsupported_backend(self):
        installed = [
            "torch_maca==0.6.0",  # maca family present
            "llama-cpp-python==0.2.90",  # gguf-style CUDA-bound backend
        ]
        capabilities = accelerator_context.get_platform_capabilities(installed)
        assert capabilities.get("maca") is True, (
            "#9: maca environment must be recognized before backend "
            "classification — maca family missing (got %r)" % capabilities
        )
        classification = accelerator_context.precheck_platform_capability(
            installed, target_family="maca"
        )
        assert classification["blocked_reason"] == "unsupported_backend", (
            "#9: GGUF model on maca without matching backend must classify "
            f"unsupported_backend — got {classification}"
        )
        assert classification["supported"] is False
        assert classification["degraded_fallback"] is False


class TestNpuRecognitionStaysIntactControl:
    """CONTROL — must PASS on current code and stay passing after T11.

    #9 RED adds musa/maca/muxi/metax assertions; this control locks the
    positive premise that existing npu recognition is NOT affected by the #9
    gap. Do NOT weaken or delete this test.
    """

    def test_npu_capability_still_recognized_control(self):
        capabilities = accelerator_context.get_platform_capabilities(
            ["torch_npu==2.1.0"]
        )
        assert capabilities.get("npu") is True
        assert capabilities.get("cpu") is False
