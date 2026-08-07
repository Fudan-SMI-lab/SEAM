from __future__ import annotations

import stat
from pathlib import Path

from harness.session.trace_export_models import OverflowCapture, OverflowStatus

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def resolve_local_reference(
    reference: str,
    allowed_roots: tuple[Path, ...],
) -> Path | OverflowCapture:
    if "\x00" in reference:
        return OverflowCapture(OverflowStatus.MALFORMED, None, "path contains NUL")
    if "://" in reference or reference.startswith(("\\\\", "//")):
        return OverflowCapture(
            OverflowStatus.REMOTE, None, "non-local output reference"
        )
    candidate = Path(reference)
    if not candidate.is_absolute():
        return OverflowCapture(OverflowStatus.RELATIVE, None, "path is not absolute")
    if ".." in candidate.parts:
        return OverflowCapture(
            OverflowStatus.PATH_TRAVERSAL, None, "path contains traversal"
        )
    try:
        if has_unsafe_ancestry(candidate):
            return OverflowCapture(
                OverflowStatus.UNSAFE, None, "linked or reparse path is not copied"
            )
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return OverflowCapture(
            OverflowStatus.MISSING, None, "source expired or missing"
        )
    except (OSError, RuntimeError) as exc:
        return OverflowCapture(OverflowStatus.READ_ERROR, None, str(exc))
    roots = _resolved_roots(allowed_roots)
    if not any(_is_within(resolved, root) for root in roots):
        return OverflowCapture(
            OverflowStatus.OUTSIDE_ALLOWED_ROOTS,
            None,
            "source is outside explicitly allowed roots",
        )
    return resolved


def has_unsafe_ancestry(path: Path) -> bool:
    for component in (path, *path.parents):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT):
            return True
    return False


def _resolved_roots(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for root in roots:
        try:
            if has_unsafe_ancestry(root):
                continue
            value = root.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            continue
        if value.is_dir():
            resolved.append(value)
    return tuple(resolved)


def _is_within(path: Path, root: Path) -> bool:
    try:
        _ = path.relative_to(root)
    except ValueError:
        return False
    return True
