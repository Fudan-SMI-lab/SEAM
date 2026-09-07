from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core.run_manifest import EvidenceDigest, ManifestErrorKind, RunManifestError
from core.run_manifest_inventory import digest_inventory
from core.run_manifest_path_models import LinkIdentity
from core.run_manifest_paths import copy_real_tree, inspect_real_tree


def _make_tree(project: Path) -> tuple[Path, Path]:
    project.mkdir()
    target = project / "target.txt"
    target.write_text("payload", encoding="utf-8")
    link = project / "link.txt"
    try:
        link.symlink_to("target.txt")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    return target, link


def test_inspect_records_in_root_relative_file_link(tmp_path: Path) -> None:
    # Given a project containing a relative in-root symlink.
    project = tmp_path / "project"
    _, link = _make_tree(project)

    # When the tree is inspected.
    tree = inspect_real_tree(project, tmp_path)

    # Then the link is recorded (not rejected) with its raw relative target.
    assert {identity.path for identity in tree.files} == {project / "target.txt"}
    assert len(tree.links) == 1
    recorded = tree.links[0]
    assert isinstance(recorded, LinkIdentity)
    assert recorded.path == link
    assert recorded.link_target == "target.txt"


def test_inspect_records_in_root_relative_dangling_link(tmp_path: Path) -> None:
    # Given a project containing a relative symlink to a missing in-root file.
    project = tmp_path / "project"
    project.mkdir()
    dangling = project / "dangling.txt"
    try:
        dangling.symlink_to("missing.txt")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # When / Then the dangling link is recorded without following it.
    tree = inspect_real_tree(project, tmp_path)
    assert [identity.path for identity in tree.links] == [dangling]
    assert tree.links[0].link_target == "missing.txt"


def test_inspect_still_rejects_absolute_target(tmp_path: Path) -> None:
    # Given a symlink whose target is an absolute path outside the root.
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = project / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # When / Then the inspection fails with a containment error.
    with pytest.raises(RunManifestError) as failure:
        _ = inspect_real_tree(project, tmp_path)
    assert failure.value.kind is ManifestErrorKind.CONTAINMENT


def test_inspect_still_rejects_escaping_relative_target(tmp_path: Path) -> None:
    # Given a symlink whose relative target escapes the evidence root.
    project = tmp_path / "project"
    project.mkdir()
    link = project / "escape.txt"
    try:
        link.symlink_to(os.path.join("..", "outside.txt"))
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # When / Then the inspection fails with a containment error.
    with pytest.raises(RunManifestError) as failure:
        _ = inspect_real_tree(project, tmp_path)
    assert failure.value.kind is ManifestErrorKind.CONTAINMENT


def test_copy_real_tree_reproduces_in_root_link(tmp_path: Path) -> None:
    # Given a project containing a relative in-root symlink.
    project = tmp_path / "project"
    _, link = _make_tree(project)
    destination = tmp_path / "copy"

    # When the tree is copied.
    copy_real_tree(project, tmp_path, destination)

    # Then the link is reproduced with the same relative target.
    reproduced = destination / link.name
    assert reproduced.is_symlink()
    assert os.readlink(reproduced) == "target.txt"
    assert (destination / "target.txt").read_bytes() == b"payload"


def test_digest_inventory_includes_in_root_link(tmp_path: Path) -> None:
    # Given a project containing a relative in-root symlink.
    project = tmp_path / "project"
    target, link = _make_tree(project)

    # When the inventory is digested.
    inventory = digest_inventory(project, tmp_path)

    # Then the link contributes a digest entry over its raw target string.
    by_path = {entry.relative_path: entry for entry in inventory}
    expected_target_digest = hashlib.sha256(b"target.txt").hexdigest()
    assert by_path["link.txt"].digest == expected_target_digest
    assert by_path["link.txt"].size_bytes == len(b"target.txt")
    assert by_path["target.txt"].digest == hashlib.sha256(target.read_bytes()).hexdigest()


def test_digest_inventory_tags_link_entries_with_link_kind(tmp_path: Path) -> None:
    # Given a project containing a relative in-root symlink.
    project = tmp_path / "project"
    target, _ = _make_tree(project)

    # When the inventory is digested.
    inventory = digest_inventory(project, tmp_path)

    # Then link entries are tagged "link" while regular files are "file".
    by_path = {entry.relative_path: entry for entry in inventory}
    assert by_path["link.txt"].kind == "link"
    assert by_path["target.txt"].kind == "file"
    assert target.read_bytes() == b"payload"


def test_link_swapped_for_identical_content_file_changes_inventory(tmp_path: Path) -> None:
    # Given a project whose link.txt points at target.txt.
    project = tmp_path / "project"
    target, link = _make_tree(project)
    original = digest_inventory(project, tmp_path)
    original_by_path = {entry.relative_path: entry for entry in original}
    original_digest = original_by_path["link.txt"].digest
    original_size = original_by_path["link.txt"].size_bytes

    # When the symlink is replaced by a regular file whose content equals the
    # former link target string (identical digest and size under the old
    # hashing scheme).
    link.unlink()
    link.write_text("target.txt", encoding="utf-8")
    replaced = digest_inventory(project, tmp_path)

    # Then the inventory changes even though digest and size are unchanged.
    replaced_by_path = {entry.relative_path: entry for entry in replaced}
    assert replaced_by_path["link.txt"].digest == original_digest
    assert replaced_by_path["link.txt"].size_bytes == original_size
    assert replaced_by_path["link.txt"].kind != original_by_path["link.txt"].kind
    assert replaced != original


def test_file_swapped_for_identical_target_link_changes_inventory(tmp_path: Path) -> None:
    # Given a project whose link.txt is a regular file whose content equals a
    # plausible link target string.
    project = tmp_path / "project"
    project.mkdir()
    target = project / "target.txt"
    target.write_text("payload", encoding="utf-8")
    link = project / "link.txt"
    link.write_text("target.txt", encoding="utf-8")
    original = digest_inventory(project, tmp_path)
    original_by_path = {entry.relative_path: entry for entry in original}
    original_digest = original_by_path["link.txt"].digest
    original_size = original_by_path["link.txt"].size_bytes

    # When the regular file is replaced by a symlink to target.txt (identical
    # digest and size under the old hashing scheme).
    link.unlink()
    link.symlink_to("target.txt")
    replaced = digest_inventory(project, tmp_path)

    # Then the inventory changes even though digest and size are unchanged.
    replaced_by_path = {entry.relative_path: entry for entry in replaced}
    assert replaced_by_path["link.txt"].digest == original_digest
    assert replaced_by_path["link.txt"].size_bytes == original_size
    assert replaced_by_path["link.txt"].kind != original_by_path["link.txt"].kind
    assert replaced != original


def test_evidence_digest_parses_legacy_record_without_kind() -> None:
    # Given a legacy sealed evidence record persisted before the kind field.
    legacy = {
        "relative_path": "artifact/summary.json",
        "digest": "a" * 64,
        "size_bytes": 12,
    }

    # When the record is parsed as an EvidenceDigest.
    parsed = EvidenceDigest.model_validate(legacy)

    # Then it defaults to a regular file so old manifests still load.
    assert parsed.kind == "file"
