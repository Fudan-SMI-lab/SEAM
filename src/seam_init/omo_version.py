"""Version gating for OMO capability resolution.

The vendored schema (commit ``ee81ab7``) reflects the v5.0.0-beta config
shape.  Only ``pluginVersion`` values whose major component is 5 are
supported; any other version (including fake strings like ``3.5.0``,
junk-with-embedded-version like ``garbage5.0.0junk``, or future
``6.0.0``) yields no capability.

Doctor's ``systemInfo.pluginVersion`` is the sole version source.  It is
a real semantic version string, never ``configPath``/``configValid``.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = [
    "SUPPORTED_VERSION_MAJOR", "VersionParseError", "is_supported_version",
    "parse_version",
]

SUPPORTED_VERSION_MAJOR: Final[int] = 5
_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.]+)?$",
)


class VersionParseError(Exception):
    """Raised when a version string cannot be parsed as semantic version."""


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse ``(major, minor, patch)`` from a semver string.

    Accepts optional ``v`` prefix and prerelease/build suffixes
    (``5.0.0-beta.5``, ``v5.1.0+build``).  Rejects junk-with-embedded-version
    (``garbage5.0.0junk``) and bare numbers (``5.0``).  Uses ``re.match``
    with an anchored end so embedded cores in garbage strings are rejected.
    """
    if not text or not text.strip():
        raise VersionParseError("empty version string")
    match = _VERSION_RE.match(text.strip())
    if match is None:
        raise VersionParseError(f"unparseable version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def is_supported_version(text: str) -> bool:
    """True iff *text* parses to a major version matching the schema generation."""
    try:
        major, _, _ = parse_version(text)
    except VersionParseError:
        return False
    return major == SUPPORTED_VERSION_MAJOR
