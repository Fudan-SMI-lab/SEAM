"""Continuation-target environment reference validation and merge rules.

The ``continuation_target`` field pins the exact Phase-2 environment the
direct runner selected as the continuation authority. It is additive and
optional on :class:`ResourceManifest` so older pre-environment manifests
remain parseable. The typed :class:`ContinuationTargetReference` carries
both the environment identifier and the Phase-2 namespace, so structural
validation can prove namespace consistency between the reference and the
recorded environment — not merely that a namespace mapping exists.

* ``validate_continuation_target`` refuses detached references and
  references whose namespace disagrees with the recorded environment's
  namespace, before any child side effect can observe them.
* ``merge_continuation_target`` makes the reference immutable once it is
  set, so the recorded Phase-2 authority cannot be redirected after the
  fact.

Both helpers are pure and raise :class:`ResourceManifestError` on
contract violation; they never touch the filesystem.
"""

from __future__ import annotations

from collections.abc import Mapping

from .resource_manifest_models import (
    ContinuationTargetReference,
    EnvironmentRecord,
    ResourceManifest,
    ResourceManifestError,
    ResourceManifestErrorKind,
)
from .resource_manifest_provenance import environment_namespace


def _error(
    kind: ResourceManifestErrorKind,
    detail: str,
) -> ResourceManifestError:
    return ResourceManifestError(kind, detail)


def validate_continuation_target(
    manifest: ResourceManifest,
    environments_by_id: Mapping[str, EnvironmentRecord],
) -> None:
    """Refuse detached, duplicate, or namespace-mismatched references.

    The continuation target is optional (``None`` is always valid). When
    present the typed reference must resolve to exactly one recorded
    environment whose recorded namespace equals the reference's own
    namespace. A bare existence check is insufficient: the namespace
    carried by the reference is the proof that the target environment is
    the one the direct runner actually selected, not a same-ID impostor
    from a different namespace.
    """
    target = manifest.continuation_target
    if target is None:
        return
    environment = environments_by_id.get(target.environment_id)
    if environment is None:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "continuation_target references an absent environment",
        )
    recorded_namespace = environment_namespace(environment)
    if recorded_namespace != target.namespace:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "continuation_target namespace differs from the recorded "
            + "environment namespace",
        )


def merge_continuation_target(
    current: ContinuationTargetReference | None,
    requested: ContinuationTargetReference | None,
) -> ContinuationTargetReference | None:
    """Resolve the continuation target across a manifest revision.

    The reference is set exactly once from the exact Phase-2 environment
    the direct runner selected. Once persisted it is immutable: an update
    that attempts to redirect it to a different reference is refused so
    the continuation authority cannot be tampered with after the fact.
    Re-asserting the same reference is permitted so re-issuing a Phase-2
    update with the same target remains a no-op.
    """
    if requested is None:
        return current
    if current is not None and requested != current:
        raise _error(
            ResourceManifestErrorKind.RUN_CONTEXT_MISMATCH,
            "continuation_target is immutable once set",
        )
    return requested
