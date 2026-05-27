"""YAML-managed accelerator platform policy for SEAM migration_utils.

Provides built-in presets and resolve/inference logic.  Users control the
active policy through the workflow YAML ``target_platform`` key; no
external profile file is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CustomOpEvidenceConfig:
    """Per-platform custom-op evidence validation parameters.

    When a field is left at its default (empty list / empty string) the
    validator falls back to the legacy NPU / Ascend behaviour.
    """

    target_device_values: list[str] = field(default_factory=list)
    """Accepted ``custom_device`` / ``target_device`` string values."""

    positive_boolean_fields: list[str] = field(default_factory=list)
    """Booleans that prove the custom path was exercised (e.g. ``npu_custom``)."""

    artifact_path_tokens: list[str] = field(default_factory=list)
    """Substrings that must appear in compiled-artifact paths."""

    native_build_log_tokens: tuple[str, ...] = ()
    """Tokens expected in the build log (case-insensitive substring match)."""

    native_source_tokens: tuple[str, ...] = ()
    """Tokens expected in source files."""

    native_binary_tokens: tuple[bytes, ...] = ()
    """Tokens expected in compiled binary artifacts."""

    native_artifact_fields: tuple[str, ...] = ()
    """Boolean fields that prove native-artifact build/presence."""

    build_log_error_message: str = ""
    """Error wording when build log lacks expected tokens."""

    binary_source_error_message: str = ""
    """Error wording when binary/source lacks expected native evidence."""

    custom_op_evidence_policy: str = ""
    """String injected into ``custom_op_evidence_policy`` prompt context."""

    # -- Performance validation configuration --------------------------------
    performance_validation: str = "full"
    """Performance validation mode: ``full`` (current strict default),
    ``presence_only`` (require timing/report presence but skip speedup
    enforcement), or ``disabled`` (skip performance validation only;
    no-fallback / source / runtime / native gates remain active)."""

    performance_baseline_device_values: list[str] = field(default_factory=lambda: ["cuda", "gpu", "torch_cuda"])
    """Accepted baseline device string values.  Configure ``["cpu", "torch_cpu"]``
    to allow CPU baselines; CPU baseline must NOT imply CPU fallback is allowed
    in the custom/migrated path."""

    performance_baseline_boolean_fields: list[str] = field(default_factory=lambda: ["cuda_baseline", "baseline_cuda", "cuda_baseline_invoked", "baseline_cuda_invoked"])
    """Boolean fields that prove a baseline path was exercised."""


@dataclass(frozen=True)
class PlatformPolicy:
    """Accelerator platform policy for migration validation and guidance."""

    id: str
    """Short machine-readable identifier (e.g. ``npu_ascend``)."""

    display_name: str
    """Human-readable label for prompts and reports."""

    custom_op_evidence: CustomOpEvidenceConfig = field(
        default_factory=CustomOpEvidenceConfig
    )

    # -- Rule migration strategy selection --
    default_rule_migration_strategy: str = "report_only"
    """Default Phase 4 rule migration strategy id for this platform.

    Strategy YAML files live in ``src/rule_strategies/``.
    The resolver checks (in order): workflow ``params.backend`` (legacy),
    workflow ``rule_migration.strategy`` (new), this field, and finally
    ``"report_only"`` as the absolute safe fallback.
    """

    # -- Guidance strings consumed by repair / operator prompts --
    guidance_prefix: str = ""
    guidance_native_label: str = ""
    guidance_native_framework: str = ""
    guidance_python_binary: str = "python"


# ---------------------------------------------------------------------------
# Legacy NPU defaults (kept for backward compatibility)
# ---------------------------------------------------------------------------

_NPU_ASCEND_EVIDENCE = CustomOpEvidenceConfig(
    target_device_values=["npu", "ascend", "torch_npu"],
    positive_boolean_fields=["npu_custom", "custom_npu", "npu_custom_invoked", "ascend_custom_invoked"],
    artifact_path_tokens=[
        "/opp/", "/op_plugin", "ascend", "cann", "acl",
        "aclnn", "aicpu", "ascendc", "custom_op", "torch_npu",
    ],
    native_build_log_tokens=(
        "aclrt",
        "aclnn",
        "acl_op",
        "-lascendcl",
        "libascendcl",
        "libacl",
        "kernel_operator.h",
        "op_host",
        "op_kernel",
        "op_proto",
        "msopgen",
        "tikcpp",
        "aicore",
        "aicpu",
    ),
    native_source_tokens=(
        "kernel_operator.h",
        "aclrt",
        "aclnn",
        "acl_op",
        "op_host",
        "op_kernel",
        "op_proto",
        "tilingdata",
        "getblockidx",
        "aicore",
        "aicpu",
    ),
    native_binary_tokens=(
        b"aclrt",
        b"aclnn",
        b"acl_op",
        b"kernel_operator",
        b"libascendcl",
        b"libacl",
        b"op_host",
        b"op_kernel",
        b"op_proto",
        b"aicore",
        b"aicpu",
    ),
    native_artifact_fields=(
        "ascend_custom_op_artifact",
        "ascend_custom_op_built",
        "native_custom_op_artifact",
        "opp_custom_op_built",
        "op_plugin_built",
        "cann_build_log_present",
        "ascendc_kernel_built",
        "tiling_kernel_built",
        "acl_op_registered",
        "aclnn_op_registered",
        "torch_npu_custom_op_loaded",
    ),
    build_log_error_message=(
        "must contain CANN/ACL/AscendC/OPP build or link evidence, not a torch-only extension build"
    ),
    binary_source_error_message=(
        "must include independent CANN/ACL/AscendC binary or source evidence; "
        "an ELF under an Ascend-looking path is not sufficient"
    ),
    custom_op_evidence_policy=(
        "require_real_ascend_cann_acl_opp_native_artifacts_no_aten_only"
    ),
)


_GENERIC_EVIDENCE = CustomOpEvidenceConfig(
    target_device_values=[
        "cuda", "gpu", "accelerator", "torch_cuda",
    ],
    positive_boolean_fields=[
        "custom", "custom_invoked", "custom_op_invoked",
        "custom_device_invoked", "native_custom",
    ],
    artifact_path_tokens=[
        "/custom_op/", "custom_op", "compiled", "extension",
        "native_build", "/build/", "/lib/",
    ],
    native_build_log_tokens=(
        "g++", "gcc", "nvcc", "clang", "clang++", "hipcc",
        "-shared", "-fPIC", "cmake", "ninja", "setuptools",
        "pip install", "build", "compile",
    ),
    native_source_tokens=(
        "#include", "kernel", "op_", "operator",
        "launch", "dispatch",
    ),
    native_binary_tokens=(
        b"ELF",
    ),
    native_artifact_fields=(
        "custom_op_artifact",
        "custom_op_built",
        "compiled_artifact",
        "native_build_artifact",
    ),
    build_log_error_message=(
        "must contain build or link evidence, not a stub-only or no-build claim"
    ),
    binary_source_error_message=(
        "must include independent binary or source evidence; "
        "a path alone is not sufficient"
    ),
    custom_op_evidence_policy="require_real_custom_op_artifacts",
)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: dict[str, PlatformPolicy] = {
    "npu_ascend": PlatformPolicy(
        id="npu_ascend",
        display_name="Ascend NPU",
        custom_op_evidence=_NPU_ASCEND_EVIDENCE,
        default_rule_migration_strategy="cuda_to_npu",
        guidance_prefix="Ascend NPU",
        guidance_native_label="Ascend NPU",
        guidance_native_framework="torch_npu / Ascend PyTorch primitives",
        guidance_python_binary="python",
    ),
    "ppu_cuda_compatible": PlatformPolicy(
        id="ppu_cuda_compatible",
        display_name="PPU (CUDA-Compatible)",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["ppu", "cuda", "gpu", "torch_cuda"],
            positive_boolean_fields=["ppu_custom", "custom_ppu", "ppu_custom_invoked", "cuda_custom", "custom_cuda"],
            artifact_path_tokens=[
                "/ppu/", "ppu_custom", "ppu_kernel", "ppu_op",
                "ppu_plugin", "ppu_extension", "ppu_compiled",
                "custom_op", "cuda", "cuda_extension",
            ],
            native_build_log_tokens=(
                "ppu",
                "ppukernel",
                "ppuccl",
                "ppucustom",
                "ppu_compiler",
                "ppu_op",
                "nvcc",
                "cuda",
                "cudart",
                "cuda_runtime",
                "-lcuda",
                "-lcudart",
            ),
            native_source_tokens=(
                "ppu",
                "ppukernel",
                "ppuccl",
                "ppucustom",
                "ppu_compiler",
                "ppu_op",
                "cuda_runtime.h",
                "cuda.h",
                "__global__",
                "cuda_kernel",
                "cublas",
            ),
            native_binary_tokens=(
                b"ppu",
                b"ppukernel",
                b"ppuccl",
                b"ppucustom",
                b"ppu_compiler",
                b"ppu_op",
                b"cuda",
                b"cudart",
                b"nvcc",
                b"CUDA",
            ),
            native_artifact_fields=(
                "ppu_custom_op_artifact",
                "ppu_custom_op_built",
                "ppu_plugin_built",
                "ppu_kernel_built",
                "ppu_custom_op_loaded",
            ),
            build_log_error_message=(
                "must contain PPU or CUDA-compatible build or link evidence, not a CPU-only build"
            ),
            binary_source_error_message=(
                "must include independent PPU/CUDA-compatible binary or source evidence; "
                "an ELF under a PPU-looking path is not sufficient"
            ),
            custom_op_evidence_policy=(
                "require_real_ppu_custom_op_artifacts"
            ),
        ),
        default_rule_migration_strategy="preserve_cuda_report_only",
        guidance_prefix="PPU (CUDA-Compatible)",
        guidance_native_label="PPU GPU",
        guidance_native_framework="torch.cuda / PPU-compatible PyTorch primitives",
        guidance_python_binary="python",
    ),
    "cuda_nvidia": PlatformPolicy(
        id="cuda_nvidia",
        display_name="NVIDIA CUDA",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["cuda", "gpu", "nvidia", "torch_cuda"],
            positive_boolean_fields=["cuda_custom", "custom_cuda", "cuda_custom_invoked"],
            artifact_path_tokens=[
                "/cuda/", "cuda_extension", "cuda_kernel",
                "cuda_op", "cublas", "cudnn",
            ],
            native_build_log_tokens=(
                "nvcc",
                "cuda",
                "cudart",
                "cublas",
                "cuda_runtime",
                "-lcuda",
                "-lcudart",
            ),
            native_source_tokens=(
                "cuda_runtime.h",
                "cuda.h",
                "__global__",
                "cuda_kernel",
                "cublas",
            ),
            native_binary_tokens=(
                b"nvcc",
                b"cuda",
                b"cudart",
                b"cublas",
                b"CUDA",
            ),
            native_artifact_fields=(
                "cuda_custom_op_artifact",
                "cuda_custom_op_built",
                "cuda_kernel_built",
                "cuda_custom_op_loaded",
            ),
            build_log_error_message=(
                "must contain CUDA/NVCC build or link evidence, not a CPU-only build"
            ),
            binary_source_error_message=(
                "must include independent CUDA binary or source evidence; "
                "an ELF under a CUDA-looking path is not sufficient"
            ),
            custom_op_evidence_policy=(
                "require_real_cuda_custom_op_artifacts"
            ),
        ),
        guidance_prefix="NVIDIA CUDA",
        guidance_native_label="NVIDIA GPU (CUDA)",
        guidance_native_framework="torch.cuda / CUDA PyTorch primitives",
        guidance_python_binary="python",
    ),
    "musa_muxi": PlatformPolicy(
        id="musa_muxi",
        display_name="MUXI MUSA",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["musa", "muxi", "musa_gpu", "maca", "metax", "mxgpu", "torch_maca"],
            positive_boolean_fields=[
                "musa_custom", "custom_musa", "musa_custom_invoked",
                "maca_custom", "custom_maca", "maca_custom_invoked", "metax_custom_invoked",
            ],
            artifact_path_tokens=[
                "/musa/", "musa_kernel", "musa_op", "musa_plugin",
                "muxi", "musart", "/maca/", "maca_kernel", "maca_op",
                "maca_plugin", "maca_extension", "metax", "mxgpu",
            ],
            native_build_log_tokens=(
                "musa",
                "muxi",
                "musart",
                "musacc",
                "musa_kernel",
                "maca",
                "metax",
                "mxgpu",
                "mxcc",
                "mccl",
                "maca_runtime",
            ),
            native_source_tokens=(
                "musa.h",
                "musart",
                "musa_runtime",
                "musa_kernel",
                "maca",
                "metax",
                "maca_runtime",
                "mxgpu",
            ),
            native_binary_tokens=(
                b"musa",
                b"muxi",
                b"musart",
                b"musacc",
                b"maca",
                b"metax",
                b"mxgpu",
                b"mxcc",
                b"mccl",
            ),
            native_artifact_fields=(
                "musa_custom_op_artifact",
                "musa_custom_op_built",
                "musa_kernel_built",
                "musa_custom_op_loaded",
                "maca_custom_op_artifact",
                "maca_custom_op_built",
                "maca_kernel_built",
                "maca_custom_op_loaded",
            ),
            build_log_error_message=(
                "must contain MUSA/MUXI/MACA build or link evidence, not a CPU-only build"
            ),
            binary_source_error_message=(
                "must include independent MUSA/MUXI/MACA binary or source evidence"
            ),
            custom_op_evidence_policy=(
                "require_real_musa_custom_op_artifacts"
            ),
        ),
        guidance_prefix="MUXI MUSA",
        guidance_native_label="MUXI GPU (MUSA)",
        guidance_native_framework="torch_musa / torch_maca / MUSA-MACA PyTorch primitives",
        guidance_python_binary="python",
    ),
    "rocm_amd": PlatformPolicy(
        id="rocm_amd",
        display_name="AMD ROCm",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["rocm", "amd", "hip", "gpu", "torch_cuda"],
            positive_boolean_fields=["rocm_custom", "custom_rocm", "hip_custom_invoked"],
            artifact_path_tokens=[
                "/rocm/", "hip_kernel", "hip_op", "rocblas",
                "miopen", "hip_extension",
            ],
            native_build_log_tokens=(
                "hipcc",
                "rocm",
                "hip_runtime",
                "rocblas",
                "hip",
                "amdgpu",
            ),
            native_source_tokens=(
                "hip_runtime.h",
                "hip/hip_runtime.h",
                "__global__",
                "hip_kernel",
                "rocblas",
            ),
            native_binary_tokens=(
                b"hipcc",
                b"rocm",
                b"hip",
                b"amdgpu",
                b"ROCm",
            ),
            native_artifact_fields=(
                "rocm_custom_op_artifact",
                "rocm_custom_op_built",
                "hip_kernel_built",
                "rocm_custom_op_loaded",
            ),
            build_log_error_message=(
                "must contain ROCm/HIP build or link evidence, not a CPU-only build"
            ),
            binary_source_error_message=(
                "must include independent ROCm/HIP binary or source evidence"
            ),
            custom_op_evidence_policy=(
                "require_real_rocm_custom_op_artifacts"
            ),
        ),
        guidance_prefix="AMD ROCm",
        guidance_native_label="AMD GPU (ROCm)",
        guidance_native_framework="torch.cuda (HIP) / ROCm PyTorch primitives",
        guidance_python_binary="python",
    ),
    "mlu_cambrian": PlatformPolicy(
        id="mlu_cambrian",
        display_name="Cambrian MLU",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["mlu", "cambrian", "cambricon"],
            positive_boolean_fields=["mlu_custom", "custom_mlu", "mlu_custom_invoked"],
            artifact_path_tokens=[
                "/mlu/", "mlu_kernel", "mlu_op", "mlu_plugin",
                "cambrian", "cnml", "cnrt",
            ],
            native_build_log_tokens=(
                "cncc",
                "cnml",
                "cnrt",
                "cambrian",
                "mlu",
                "bangc",
            ),
            native_source_tokens=(
                "cnml.h",
                "cnrt.h",
                "cambrian",
                "mlu_kernel",
                "bangc",
            ),
            native_binary_tokens=(
                b"cncc",
                b"cnml",
                b"cnrt",
                b"cambrian",
                b"MLU",
            ),
            native_artifact_fields=(
                "mlu_custom_op_artifact",
                "mlu_custom_op_built",
                "mlu_kernel_built",
                "mlu_custom_op_loaded",
            ),
            build_log_error_message=(
                "must contain Cambrian/MLU build or link evidence, not a CPU-only build"
            ),
            binary_source_error_message=(
                "must include independent Cambrian/MLU binary or source evidence"
            ),
            custom_op_evidence_policy=(
                "require_real_mlu_custom_op_artifacts"
            ),
        ),
        guidance_prefix="Cambrian MLU",
        guidance_native_label="Cambrian MLU",
        guidance_native_framework="torch_mlu / Cambrian PyTorch primitives",
        guidance_python_binary="python",
    ),
    "generic_accelerator": PlatformPolicy(
        id="generic_accelerator",
        display_name="Generic Accelerator",
        custom_op_evidence=_GENERIC_EVIDENCE,
        guidance_prefix="Generic Accelerator",
        guidance_native_label="Target Accelerator",
        guidance_native_framework="target accelerator PyTorch primitives",
        guidance_python_binary="python",
    ),
}


# ---------------------------------------------------------------------------
# Legacy / backward-compatible token tuples (module-level for existing code)
# ---------------------------------------------------------------------------

# These are the *default* NPU tokens.  When a PlatformPolicy is active and
# its custom_op_evidence carries non-empty token tuples, the validator
# should prefer those.  The module-level constants are kept here so that
# existing importers of validate_validation_final continue to work.

LEGACY_NPU_ARTIFACT_PATH_TOKENS = (
    "/opp/",
    "/op_plugin",
    "ascend",
    "cann",
    "acl",
    "aclnn",
    "aicpu",
    "ascendc",
    "custom_op",
    "torch_npu",
)

LEGACY_NPU_BUILD_LOG_TOKENS = _NPU_ASCEND_EVIDENCE.native_build_log_tokens
LEGACY_NPU_SOURCE_TOKENS = _NPU_ASCEND_EVIDENCE.native_source_tokens
LEGACY_NPU_BINARY_TOKENS = _NPU_ASCEND_EVIDENCE.native_binary_tokens
LEGACY_NPU_ARTIFACT_FIELDS = _NPU_ASCEND_EVIDENCE.native_artifact_fields


# ---------------------------------------------------------------------------
# Helpers for the YAML-driven API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetPlatformConfig:
    """Parsed ``target_platform`` block from workflow YAML."""

    preset: str
    overrides: dict[str, Any] = field(default_factory=dict)


def parse_target_platform(raw: Any) -> TargetPlatformConfig | None:
    """Parse a ``target_platform`` YAML key into a TargetPlatformConfig.

    Returns ``None`` when the key is absent or ``None``.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"target_platform must be a mapping, got {type(raw).__name__}"
        )
    preset = raw.get("preset")
    if not preset or not isinstance(preset, str):
        raise ValueError("target_platform.preset must be a non-empty string")
    overrides = raw.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("target_platform.overrides must be a mapping")
    return TargetPlatformConfig(
        preset=str(preset).strip(),
        overrides=dict(overrides) if overrides else {},
    )


def resolve_policy(target_platform: TargetPlatformConfig | None, workflow_name: str) -> PlatformPolicy:
    """Resolve the active PlatformPolicy for a workflow.

    1. If ``target_platform`` is provided, resolve from built-in presets.
    2. Otherwise, infer from the workflow name (backward compatibility).
    """
    if target_platform is not None:
        preset_id = target_platform.preset
        if preset_id not in BUILTIN_PRESETS:
            raise ValueError(
                f"Unknown platform preset '{preset_id}'. "
                f"Known presets: {sorted(BUILTIN_PRESETS)}"
            )
        base = BUILTIN_PRESETS[preset_id]
        if target_platform.overrides:
            base = _apply_overrides(base, target_platform.overrides)
        return base
    return _infer_policy_by_name(workflow_name)


def _infer_policy_by_name(name: str) -> PlatformPolicy:
    """Infer platform policy from workflow name for backward compatibility.

    * ``npu_migration*`` → ``npu_ascend``
    * ``ppu_migration*`` → ``ppu_cuda_compatible``
    * otherwise → ``generic_accelerator``
    """
    name_lower = name.strip().lower()
    if name_lower.startswith("npu_migration"):
        return BUILTIN_PRESETS["npu_ascend"]
    if name_lower.startswith("ppu_migration"):
        return BUILTIN_PRESETS["ppu_cuda_compatible"]
    return BUILTIN_PRESETS["generic_accelerator"]


def _apply_overrides(base: PlatformPolicy, overrides: dict[str, Any]) -> PlatformPolicy:
    """Apply user-supplied overrides from workflow YAML to a base policy.

    Only a whitelisted set of override keys is honoured to keep the
    implementation simple and secure.
    """
    overridden_id = overrides.get("id")
    overridden_display_name = overrides.get("display_name")

    ce_overrides = overrides.get("custom_op_evidence")
    ce = base.custom_op_evidence
    if isinstance(ce_overrides, dict):
        ce = CustomOpEvidenceConfig(
            target_device_values=_list_override(
                ce_overrides, "target_device_values", ce.target_device_values
            ),
            positive_boolean_fields=_list_override(
                ce_overrides, "positive_boolean_fields", ce.positive_boolean_fields
            ),
            artifact_path_tokens=_list_override(
                ce_overrides, "artifact_path_tokens", list(ce.artifact_path_tokens)
            ),
            native_build_log_tokens=tuple(
                _list_override(ce_overrides, "native_build_log_tokens", list(ce.native_build_log_tokens))
            ),
            native_source_tokens=tuple(
                _list_override(ce_overrides, "native_source_tokens", list(ce.native_source_tokens))
            ),
            native_binary_tokens=tuple(
                _bytes_list_override(ce_overrides, "native_binary_tokens", list(ce.native_binary_tokens))
            ),
            native_artifact_fields=tuple(
                _list_override(ce_overrides, "native_artifact_fields", list(ce.native_artifact_fields))
            ),
            build_log_error_message=str(
                ce_overrides.get("build_log_error_message", ce.build_log_error_message)
            ),
            binary_source_error_message=str(
                ce_overrides.get("binary_source_error_message", ce.binary_source_error_message)
            ),
            custom_op_evidence_policy=str(
                ce_overrides.get("custom_op_evidence_policy", ce.custom_op_evidence_policy)
            ),
            performance_validation=str(
                ce_overrides.get("performance_validation", ce.performance_validation)
            ),
            performance_baseline_device_values=_list_override(
                ce_overrides, "performance_baseline_device_values", ce.performance_baseline_device_values
            ),
            performance_baseline_boolean_fields=_list_override(
                ce_overrides, "performance_baseline_boolean_fields", ce.performance_baseline_boolean_fields
            ),
        )

    overridden_strategy = overrides.get("default_rule_migration_strategy")

    return PlatformPolicy(
        id=str(overridden_id) if isinstance(overridden_id, str) else base.id,
        display_name=str(overridden_display_name) if isinstance(overridden_display_name, str) else base.display_name,
        custom_op_evidence=ce,
        default_rule_migration_strategy=(
            str(overridden_strategy)
            if isinstance(overridden_strategy, str) and overridden_strategy.strip()
            else base.default_rule_migration_strategy
        ),
        guidance_prefix=str(overrides.get("guidance_prefix", base.guidance_prefix)),
        guidance_native_label=str(overrides.get("guidance_native_label", base.guidance_native_label)),
        guidance_native_framework=str(overrides.get("guidance_native_framework", base.guidance_native_framework)),
        guidance_python_binary=str(overrides.get("guidance_python_binary", base.guidance_python_binary)),
    )


def _list_override(overrides: dict[str, Any], key: str, default: list[str]) -> list[str]:
    """Return an override list value or the default."""
    val = overrides.get(key)
    if isinstance(val, list):
        return [str(item) for item in val if item is not None]
    return default


def _bytes_list_override(overrides: dict[str, Any], key: str, default: list[bytes]) -> list[bytes]:
    """Return an override list of bytes or the default."""
    val = overrides.get(key)
    if isinstance(val, list):
        result: list[bytes] = []
        for item in val:
            if isinstance(item, bytes):
                result.append(item)
            elif isinstance(item, str):
                result.append(item.encode("utf-8", errors="replace"))
        return result if result else default
    return default


# ---------------------------------------------------------------------------
# Policy → validator token helpers
# ---------------------------------------------------------------------------

def get_artifact_path_tokens(policy: PlatformPolicy | None) -> list[str]:
    """Return artifact path tokens.  Only falls back to legacy NPU when *policy*
    is ``None`` (old YAML with no ``target_platform``).  An explicit policy
    always uses its own tokens, even when empty."""
    if policy is None:
        return list(LEGACY_NPU_ARTIFACT_PATH_TOKENS)
    return list(policy.custom_op_evidence.artifact_path_tokens)


def get_native_build_log_tokens(policy: PlatformPolicy | None) -> tuple[str, ...]:
    """Return native build log tokens.  Only falls back to legacy NPU when
    *policy* is ``None``."""
    if policy is None:
        return LEGACY_NPU_BUILD_LOG_TOKENS
    return policy.custom_op_evidence.native_build_log_tokens


def get_native_source_tokens(policy: PlatformPolicy | None) -> tuple[str, ...]:
    """Return native source tokens.  Only falls back to legacy NPU when
    *policy* is ``None``."""
    if policy is None:
        return LEGACY_NPU_SOURCE_TOKENS
    return policy.custom_op_evidence.native_source_tokens


def get_native_binary_tokens(policy: PlatformPolicy | None) -> tuple[bytes, ...]:
    """Return native binary tokens.  Only falls back to legacy NPU when
    *policy* is ``None``."""
    if policy is None:
        return LEGACY_NPU_BINARY_TOKENS
    return policy.custom_op_evidence.native_binary_tokens


def get_target_device_values(policy: PlatformPolicy | None) -> list[str]:
    """Return accepted target device values.  Only falls back to legacy NPU
    when *policy* is ``None``."""
    if policy is None:
        return ["npu", "ascend", "torch_npu"]
    return list(policy.custom_op_evidence.target_device_values)


def get_positive_boolean_fields(policy: PlatformPolicy | None) -> list[str]:
    """Return positive boolean fields for custom-device proof.  Only falls
    back to legacy NPU when *policy* is ``None``."""
    if policy is None:
        return ["npu_custom", "custom_npu", "npu_custom_invoked", "ascend_custom_invoked"]
    return list(policy.custom_op_evidence.positive_boolean_fields)


def get_performance_validation_mode(policy: PlatformPolicy | None) -> str:
    """Return the performance validation mode string.

    Valid modes: ``full``, ``presence_only``, ``disabled``.
    Defaults to ``full`` when *policy* is ``None``.  Normalizes case and whitespace.
    """
    if policy is None:
        return "full"
    mode = policy.custom_op_evidence.performance_validation.strip().lower()
    if mode in ("full", "presence_only", "disabled"):
        return mode
    return "full"


def get_performance_baseline_device_values(policy: PlatformPolicy | None) -> set[str]:
    """Return accepted baseline device values for baseline proof checks.
    Defaults to ``{"cuda", "gpu", "torch_cuda"}`` when *policy* is ``None``.
    """
    if policy is None:
        return {"cuda", "gpu", "torch_cuda"}
    return set(policy.custom_op_evidence.performance_baseline_device_values)


def get_performance_baseline_boolean_fields(policy: PlatformPolicy | None) -> list[str]:
    """Return boolean fields that prove a baseline path was exercised.
    Defaults to CUDA baseline fields when *policy* is ``None``.
    """
    if policy is None:
        return ["cuda_baseline", "baseline_cuda", "cuda_baseline_invoked", "baseline_cuda_invoked"]
    return list(policy.custom_op_evidence.performance_baseline_boolean_fields)
