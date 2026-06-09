"""YAML-managed accelerator platform policy for SEAM migration_utils.

Provides built-in presets and resolve/inference logic.  Users control the
active policy through the workflow YAML ``target_platform`` key; no
external profile file is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CustomOpEvidenceConfig:
    """Per-platform custom-op evidence validation parameters.
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

    strict_producer_closure_required: bool = False
    """Whether final gate must run the strict producer-artifact closure."""

    preflight_project_evidence_required: bool = False
    """Whether entry-script execution should fail closed before running when project-local native evidence is absent."""

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

    # -- File-level validation tweaks -----------------------------------------
    validated_config_files: tuple[str, ...] = ()
    """Canonical config file basenames that are expected/validated for this platform
    (e.g. ``npu_supported_ops.json`` for NPU).  Used by custom-op final-gate
    validators instead of hardcoded platform checks."""

    skip_patterns: tuple[str, ...] = ()
    """Substrings that cause a path/entry to be skipped during evidence scanning
    (e.g. ``npuextension_only`` for NPU)."""


@dataclass(frozen=True)
class EnvValidationConfig:
    """Per-platform environment-detection validation requirements.

    Replaces hardcoded ``if platform_key == "npu"`` branches in
    ``validate_env_detect.py`` with declarative per-preset config.
    """

    detection_field: str = ""
    """Boolean field that proves the platform was detected (e.g. ``npu_detected``)."""

    required_string_fields: tuple[str, ...] = ()
    """String fields that MUST be non-empty for this platform
    (e.g. ``cann_version``, ``driver_version`` for NPU)."""

    required_bool_fields: tuple[str, ...] = ()
    """Boolean fields that MUST be present and True-ish for this platform
    (e.g. ``ascendc_available`` for NPU)."""

    optional_string_fields: tuple[str, ...] = ()
    """String fields that are optional but must be str when present."""

    optional_bool_fields: tuple[str, ...] = ()
    """Boolean fields that are optional but must be bool when present."""




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

    env_validation: EnvValidationConfig = field(
        default_factory=EnvValidationConfig
    )

    # -- Rule migration strategy selection --
    default_rule_migration_strategy: str = "report_only"
    """Default Phase 4 rule migration strategy id for this platform.

    Strategy YAML files live in ``src/rule_strategies/``.
    The resolver checks (in order): workflow ``params.backend`` (legacy),
    workflow ``rule_migration.strategy`` (new), this field, and finally
    ``"report_only"`` as the absolute safe fallback.
    """

    rule_based_migrator_module: str = ""
    """Fully-qualified module name for the platform's rule-based migrator
    (e.g. ``migrator.rule_based_ppu``).  When set together with
    ``rule_based_migrator_class``, the ``ppu_rule_based_migration`` builtin
    operation dynamically imports and instantiates this migrator instead of
    falling back to a report-only migrator."""

    rule_based_migrator_class: str = ""
    """Class name of the platform's rule-based migrator (e.g.
    ``PPURuleBasedMigrator``).  Only used when ``rule_based_migrator_module``
    is also non-empty."""

    # -- Prompt fallback configuration --
    prompt_fallback_suffixes: tuple[str, ...] = ()
    """Suffixes tried when a phase's primary prompt template is missing.  When
    empty the caller falls back to ``routes.DEFAULT_PROMPT_FALLBACK_SUFFIXES``."""

    framework_env_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per-framework env-var defaults merged on top of
    ``routes.FRAMEWORK_SERVING_ENV_DEFAULTS`` (platform-specific additions)."""

    framework_forbidden_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Per-framework forbidden runtime markers merged on top of
    ``routes.FRAMEWORK_FORBIDDEN_RUNTIME_MARKERS``."""

    # -- Guidance strings consumed by repair / operator prompts --
    guidance_native_label: str = ""
    guidance_native_framework: str = ""
    repair_prompt_ids: dict[str, str] = field(default_factory=dict)
    repair_prompt_ids_container: dict[str, str] = field(default_factory=dict)
    error_analyzer_prompt_id: str = ""
    error_analyzer_prompt_id_container: str = ""


_NPU_ASCEND_EVIDENCE = CustomOpEvidenceConfig(
    target_device_values=["npu", "ascend", "torch_npu"],
    positive_boolean_fields=["npu_custom", "custom_npu", "npu_custom_invoked", "ascend_custom_invoked"],
    performance_baseline_device_values=["cpu", "torch_cpu", "python_cpu", "cpu_reference"],
    performance_baseline_boolean_fields=["cpu_baseline", "baseline_cpu", "cpu_baseline_invoked", "baseline_cpu_invoked"],
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
    strict_producer_closure_required=True,
    preflight_project_evidence_required=True,
    validated_config_files=(
        "binary_info_config.json",
        "aic-ascend*-ops-info.json",
        "npu_supported_ops.json",
    ),
    skip_patterns=("npuextension_only",),
)


_NPU_ASCEND_ENV_VALIDATION = EnvValidationConfig(
    detection_field="npu_detected",
    required_string_fields=("cann_version", "driver_version"),
    required_bool_fields=("ascendc_available",),
)

_PPU_CUDA_ENV_VALIDATION = EnvValidationConfig(
    detection_field="ppu_detected",
    required_bool_fields=("cuda_api_available",),
    optional_string_fields=("cann_version", "driver_version"),
    optional_bool_fields=("ascendc_available",),
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
        env_validation=_NPU_ASCEND_ENV_VALIDATION,
        default_rule_migration_strategy="cuda_to_npu",
        guidance_native_label="Ascend NPU",
        guidance_native_framework="torch_npu / Ascend PyTorch primitives",
        repair_prompt_ids={
            "dependency_fixer": "repair_dependency_fixer_npu",
            "code_adapter": "repair_code_adapter_npu",
            "operator_fixer": "repair_operator_fixer_npu",
        },
        repair_prompt_ids_container={
            "dependency_fixer": "repair_dependency_fixer_container_npu",
            "code_adapter": "repair_code_adapter_container_npu",
            "operator_fixer": "repair_operator_fixer_container_npu",
        },
        error_analyzer_prompt_id="phase_error_recovery_npu",
        error_analyzer_prompt_id_container="phase_error_recovery_container_npu",
    ),
    "ppu_cuda_compatible": PlatformPolicy(
        id="ppu_cuda_compatible",
        display_name="PPU (CUDA-Compatible)",
        rule_based_migrator_module="migrator.rule_based_ppu",
        rule_based_migrator_class="PPURuleBasedMigrator",
        env_validation=_PPU_CUDA_ENV_VALIDATION,
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
        guidance_native_label="PPU GPU",
        guidance_native_framework="torch.cuda / PPU-compatible PyTorch primitives",
        repair_prompt_ids_container={
            "dependency_fixer": "repair_dependency_fixer_container_ppu",
            "code_adapter": "repair_code_adapter_container_ppu",
            "operator_fixer": "repair_operator_fixer_container_ppu",
        },
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
        guidance_native_label="NVIDIA GPU (CUDA)",
        guidance_native_framework="torch.cuda / CUDA PyTorch primitives",
    ),
    "musa_muxi": PlatformPolicy(
        id="musa_muxi",
        display_name="MUXI MUSA",
        custom_op_evidence=CustomOpEvidenceConfig(
            target_device_values=["musa", "muxi", "musa_gpu", "maca", "metax", "mxgpu", "torch_musa", "torch_maca"],
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
        guidance_native_label="MUXI GPU (MUSA)",
        guidance_native_framework="torch_musa / torch_maca / MUSA-MACA PyTorch primitives",
        repair_prompt_ids_container={
            "dependency_fixer": "repair_dependency_fixer_container_musa",
            "code_adapter": "repair_code_adapter_container_musa",
            "operator_fixer": "repair_operator_fixer_container_musa",
        },
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
        guidance_native_label="AMD GPU (ROCm)",
        guidance_native_framework="torch.cuda (HIP) / ROCm PyTorch primitives",
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
        guidance_native_label="Cambrian MLU",
        guidance_native_framework="torch_mlu / Cambrian PyTorch primitives",
    ),
    "generic_accelerator": PlatformPolicy(
        id="generic_accelerator",
        display_name="Generic Accelerator",
        custom_op_evidence=_GENERIC_EVIDENCE,
        guidance_native_label="Target Accelerator",
        guidance_native_framework="target accelerator PyTorch primitives",
    ),
}




# ---------------------------------------------------------------------------
# Helpers for the YAML-driven API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetPlatformConfig:
    """Parsed ``target_platform`` block from workflow YAML."""

    preset: str
    overrides: dict[str, object] = field(default_factory=dict)


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
    raw_map = cast(dict[object, object], raw)
    preset = raw_map.get("preset")
    if not preset or not isinstance(preset, str):
        raise ValueError("target_platform.preset must be a non-empty string")
    overrides = raw_map.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("target_platform.overrides must be a mapping")
    return TargetPlatformConfig(
        preset=str(preset).strip(),
        overrides=cast(dict[str, object], overrides) if overrides else {},
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


# ── Workflow-name → preset mapping ────────────────────────────────────
# Ordered from most-specific to least-specific so that the first
# matching prefix wins.  Keys are lowercased workflow-name prefixes;
# values are BUILTIN_PRESETS keys.
_INFER_MAP: tuple[tuple[str, str], ...] = (
    ("musa_muxi_migration", "musa_muxi"),
    ("ppu_migration", "ppu_cuda_compatible"),
    ("npu_migration", "npu_ascend"),
)


def _infer_policy_by_name(name: str) -> PlatformPolicy:
    """Infer platform policy from workflow name for backward compatibility.

    The matching table is defined in ``_INFER_MAP`` so that adding
    support for a new platform only requires inserting an entry into
    the tuple — no code changes needed.

    When no prefix matches, ``generic_accelerator`` is returned.
    """
    name_lower = name.strip().lower()
    for prefix, preset_key in _INFER_MAP:
        if name_lower.startswith(prefix):
            return BUILTIN_PRESETS[preset_key]
    return BUILTIN_PRESETS["generic_accelerator"]


def _apply_overrides(base: PlatformPolicy, overrides: dict[str, object]) -> PlatformPolicy:
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
            strict_producer_closure_required=_bool_override(
                ce_overrides,
                "strict_producer_closure_required",
                ce.strict_producer_closure_required,
            ),
            preflight_project_evidence_required=_bool_override(
                ce_overrides,
                "preflight_project_evidence_required",
                ce.preflight_project_evidence_required,
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
        prompt_fallback_suffixes=_tuple_str_override(overrides, "prompt_fallback_suffixes", base.prompt_fallback_suffixes),
        framework_env_overrides=_dict_dict_override(overrides, "framework_env_overrides", base.framework_env_overrides),
        framework_forbidden_markers=_dict_tuple_override(overrides, "framework_forbidden_markers", base.framework_forbidden_markers),
        guidance_native_label=str(overrides.get("guidance_native_label", base.guidance_native_label)),
        guidance_native_framework=str(overrides.get("guidance_native_framework", base.guidance_native_framework)),
        repair_prompt_ids=_dict_str_override(overrides, "repair_prompt_ids", base.repair_prompt_ids),
        repair_prompt_ids_container=_dict_str_override(overrides, "repair_prompt_ids_container", base.repair_prompt_ids_container),
        error_analyzer_prompt_id=_string_override(overrides, "error_analyzer_prompt_id", base.error_analyzer_prompt_id),
        error_analyzer_prompt_id_container=_string_override(overrides, "error_analyzer_prompt_id_container", base.error_analyzer_prompt_id_container),
    )


def _string_override(overrides: dict[str, object], key: str, default: str) -> str:
    value = overrides.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _bool_override(overrides: dict[str, object], key: str, default: bool) -> bool:
    value = overrides.get(key)
    return value if isinstance(value, bool) else default


def _dict_str_override(overrides: dict[str, object], key: str, default: dict[str, str]) -> dict[str, str]:
    value = overrides.get(key)
    if isinstance(value, dict):
        return {str(item_key): str(item_value) for item_key, item_value in value.items() if item_value is not None}
    return dict(default)


def _list_override(overrides: dict[str, object], key: str, default: list[str]) -> list[str]:
    """Return an override list value or the default."""
    val = overrides.get(key)
    if isinstance(val, list):
        return [str(item) for item in val if item is not None]
    return default


def _bytes_list_override(overrides: dict[str, object], key: str, default: list[bytes]) -> list[bytes]:
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
    """Return artifact path tokens for an explicit policy or generic fallback."""
    if policy is None:
        return list(_GENERIC_EVIDENCE.artifact_path_tokens)
    return list(policy.custom_op_evidence.artifact_path_tokens)


def get_native_build_log_tokens(policy: PlatformPolicy | None) -> tuple[str, ...]:
    """Return native build log tokens for an explicit policy or generic fallback."""
    if policy is None:
        return _GENERIC_EVIDENCE.native_build_log_tokens
    return policy.custom_op_evidence.native_build_log_tokens


def get_native_source_tokens(policy: PlatformPolicy | None) -> tuple[str, ...]:
    """Return native source tokens for an explicit policy or generic fallback."""
    if policy is None:
        return _GENERIC_EVIDENCE.native_source_tokens
    return policy.custom_op_evidence.native_source_tokens


def get_native_binary_tokens(policy: PlatformPolicy | None) -> tuple[bytes, ...]:
    """Return native binary tokens for an explicit policy or generic fallback."""
    if policy is None:
        return _GENERIC_EVIDENCE.native_binary_tokens
    return policy.custom_op_evidence.native_binary_tokens


def get_target_device_values(policy: PlatformPolicy | None) -> list[str]:
    """Return accepted target device values for an explicit policy or generic fallback."""
    if policy is None:
        return list(_GENERIC_EVIDENCE.target_device_values)
    return list(policy.custom_op_evidence.target_device_values)


def get_positive_boolean_fields(policy: PlatformPolicy | None) -> list[str]:
    """Return positive boolean fields for an explicit policy or generic fallback."""
    if policy is None:
        return list(_GENERIC_EVIDENCE.positive_boolean_fields)
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

    Raises:
        ValueError: When *policy* is ``None`` — a PlatformPolicy matching the
            target platform must be provided to determine the correct baseline
            device values.
    """
    if policy is None:
        raise ValueError(
            "No PlatformPolicy provided — cannot determine performance baseline "
            + "device values. Provide a PlatformPolicy matching the target platform."
        )
    return set(policy.custom_op_evidence.performance_baseline_device_values)


def get_performance_baseline_boolean_fields(policy: PlatformPolicy | None) -> list[str]:
    """Return boolean fields that prove a baseline path was exercised.

    Raises:
        ValueError: When *policy* is ``None`` — a PlatformPolicy matching the
            target platform must be provided to determine the correct baseline
            boolean fields.
    """
    if policy is None:
        raise ValueError(
            "No PlatformPolicy provided — cannot determine performance baseline "
            + "boolean fields. Provide a PlatformPolicy matching the target platform."
        )
    return list(policy.custom_op_evidence.performance_baseline_boolean_fields)


def _tuple_str_override(overrides: dict[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    val = overrides.get(key)
    if isinstance(val, (list, tuple)):
        return tuple(str(item) for item in val if isinstance(item, str) and item.strip())
    return default


def _dict_dict_override(overrides: dict[str, object], key: str, default: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    val = overrides.get(key)
    if isinstance(val, dict):
        result: dict[str, dict[str, str]] = {}
        for framework, env_map in val.items():
            if isinstance(env_map, dict):
                result[str(framework)] = {str(k): str(v) for k, v in env_map.items() if v is not None}
        return result if result else dict(default)
    return dict(default)


def _dict_tuple_override(overrides: dict[str, object], key: str, default: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    val = overrides.get(key)
    if isinstance(val, dict):
        result: dict[str, tuple[str, ...]] = {}
        for framework, markers in val.items():
            if isinstance(markers, (list, tuple)):
                result[str(framework)] = tuple(str(m) for m in markers if isinstance(m, str) and m.strip())
        return result if result else dict(default)
    return dict(default)
