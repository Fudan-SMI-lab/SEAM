"""Source-backed custom-op unit discovery shared by validation and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


NATIVE_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".cu", ".cuh", ".h", ".hh", ".hpp"}
CUDA_SOURCE_SUFFIXES = {".cu", ".cuh"}
EXCLUDED_SOURCE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".sm-artifacts",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "output_projects",
    "site-packages",
    "venv",
}
MAX_DISCOVERY_FILES = 2000
MAX_DISCOVERY_BYTES = 2_000_000
CUDA_NATIVE_SUFFIXES = ("_cuda", "_gpu")


def _get_native_suffixes(target_device_values: frozenset[str] | None = None) -> tuple[str, ...]:
    """Return platform-appropriate native source suffixes derived from *target_device_values*.

    When *target_device_values* is ``None``, returns the default ``("_cuda", "_gpu")``.
    Otherwise extends the defaults with platform-specific suffixes:
    ``"npu"`` adds ``"_npu"``, ``"musa"`` adds ``"_musa"``, etc.
    """
    if target_device_values is None:
        return CUDA_NATIVE_SUFFIXES
    suffix_map: dict[str, str] = {
        "npu": "_npu",
        "musa": "_musa",
        "rocm": "_hip",
        "mlu": "_mlu",
    }
    extra = tuple(
        suffix_map[v]
        for v in sorted(target_device_values)
        if v in suffix_map and suffix_map[v] not in CUDA_NATIVE_SUFFIXES
    )
    return CUDA_NATIVE_SUFFIXES + extra


RETURN_TYPE_PATTERN = (
    r"(?:extern\s+\"C\"\s+)?"
    r"(?:(?:static|inline|constexpr|__host__|__device__|__global__|__forceinline__)\s+)*"
    r"(?:void|int|long|float|double|bool|size_t|auto|"
    r"[A-Za-z_]\w*(?:::\w+)*(?:\s*<[^;{}()]+>)?(?:\s*[*&])*)"
)
MACRO_EXPORT_PATTERN = re.compile(
    RETURN_TYPE_PATTERN
    + r"\s+(?P<macro>[A-Za-z_]\w*_FUNC|FUNC)\s*\(\s*(?P<name>[A-Za-z_]\w*)\s*\)\s*\("
)
PLAIN_EXPORT_PATTERN = re.compile(
    RETURN_TYPE_PATTERN
    + r"\s+(?P<name>[A-Za-z_]\w*(?:_cuda|_gpu))\s*\("
)
PYBIND_DEF_PATTERN = re.compile(r'\bm\.def\s*\(\s*["\'](?P<name>[A-Za-z_]\w*)["\']')


@dataclass(frozen=True)
class NativeUnit:
    identity: str
    family: str
    symbol: str
    source_path: str
    line_number: int


def discover_required_cuda_native_units_from_project(project_dir: object) -> list[NativeUnit]:
    if not isinstance(project_dir, str) or not project_dir.strip():
        return []
    root = Path(project_dir)
    if not root.is_dir():
        return []
    return discover_required_cuda_native_units(root)


def discover_required_cuda_native_units(project_dir: Path) -> list[NativeUnit]:
    files = _native_source_files(project_dir)
    if not files:
        return []

    contents: dict[Path, str] = {}
    for path in files:
        try:
            if path.stat().st_size > MAX_DISCOVERY_BYTES:
                continue
            contents[path] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    if not contents:
        return []

    cuda_text = "\n".join(text for path, text in contents.items() if path.suffix.lower() in CUDA_SOURCE_SUFFIXES)
    units: dict[str, NativeUnit] = {}
    for path, text in contents.items():
        relative = path.relative_to(project_dir).as_posix()
        for unit in _extract_native_units_from_text(relative, text, path.suffix.lower(), cuda_text):
            _ = units.setdefault(unit.identity, unit)
    return sorted(units.values(), key=lambda unit: unit.identity)


def _native_source_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(project_dir.rglob("*"), key=lambda item: item.relative_to(project_dir).as_posix()):
        if len(files) >= MAX_DISCOVERY_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in NATIVE_SOURCE_SUFFIXES:
            continue
        relative_parts = path.relative_to(project_dir).parts
        if any(part in EXCLUDED_SOURCE_DIRS for part in relative_parts):
            continue
        files.append(path)
    return files


def _extract_native_units_from_text(
    relative_path: str,
    text: str,
    suffix: str,
    cuda_text: str,
) -> list[NativeUnit]:
    units: list[NativeUnit] = []
    for match in MACRO_EXPORT_PATTERN.finditer(text):
        macro = match.group("macro")
        name = match.group("name")
        if not _macro_unit_is_cuda_related(macro, name, text, suffix, cuda_text):
            continue
        family = _family_from_path(relative_path)
        if macro != "FUNC":
            family = _family_from_macro(macro, family)
        symbol = _cuda_symbol_name(macro, name, suffix)
        units.append(
            NativeUnit(
                identity=f"{family}:{symbol}",
                family=family,
                symbol=symbol,
                source_path=relative_path,
                line_number=_line_number(text, match.start()),
            )
        )

    for match in PLAIN_EXPORT_PATTERN.finditer(text):
        name = match.group("name")
        family = _family_from_path(relative_path)
        units.append(
            NativeUnit(
                identity=f"{family}:{name}",
                family=family,
                symbol=name,
                source_path=relative_path,
                line_number=_line_number(text, match.start()),
            )
        )

    if "PYBIND11_MODULE" in text and cuda_text:
        family = _family_from_path(relative_path)
        for match in PYBIND_DEF_PATTERN.finditer(text):
            name = match.group("name")
            if not _pybind_symbol_is_cuda_related(name, cuda_text):
                continue
            units.append(
                NativeUnit(
                    identity=f"{family}:{name}",
                    family=family,
                    symbol=name,
                    source_path=relative_path,
                    line_number=_line_number(text, match.start()),
                )
            )
    return units


def _pybind_symbol_is_cuda_related(name: str, cuda_text: str) -> bool:
    tokens = [token for token in re.split(r"_+", name.lower()) if token]
    if not tokens:
        return False
    symbol_pattern = r"[A-Za-z_]\w*(?:kernel|cuda|gpu|wrapper)[A-Za-z0-9_]*"
    for match in re.finditer(symbol_pattern, cuda_text, flags=re.IGNORECASE):
        symbol = match.group(0).lower()
        if all(token in symbol for token in tokens):
            return True
    return False


def _macro_unit_is_cuda_related(macro: str, name: str, text: str, suffix: str, cuda_text: str) -> bool:
    name_lower = name.lower()
    if name_lower.endswith(CUDA_NATIVE_SUFFIXES):
        return True
    if macro == "FUNC":
        return suffix in CUDA_SOURCE_SUFFIXES and bool(
            re.search(r"#define\s+CAT_I\b[^\n]*(?:cuda|gpu|device|dw_device)", text, flags=re.IGNORECASE)
        )
    invocation = f"{macro}({name})"
    return invocation in cuda_text


def _cuda_symbol_name(macro: str, name: str, suffix: str) -> str:
    if name.lower().endswith(CUDA_NATIVE_SUFFIXES):
        return name
    if macro == "FUNC" and suffix in CUDA_SOURCE_SUFFIXES:
        return f"{name}_cuda"
    return name


def _family_from_macro(macro: str, fallback: str) -> str:
    prefix = macro[:-5].lower() if macro.endswith("_FUNC") else macro.lower()
    if prefix == "sc":
        return "simple_compress"
    if prefix == "storage":
        return "storage"
    return prefix or fallback


def _family_from_path(relative_path: str) -> str:
    stem = Path(relative_path).stem
    if stem.endswith("_utils"):
        stem = stem[:-6]
    return stem


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
