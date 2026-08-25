from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core.run_manifest import ManifestErrorKind, RunManifestError
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
