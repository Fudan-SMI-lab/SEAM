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
