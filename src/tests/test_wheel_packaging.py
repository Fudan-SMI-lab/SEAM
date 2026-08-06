"""RED tests for bug #10 — wheel packaging (T14).

Bug #10: the project cannot be installed from a wheel as a usable ``seam``
package. Current-behavior divergences these RED tests lock:

1. ``[project.scripts]`` entry missing: ``src/pyproject.toml`` (51 lines) has
   NO ``[project.scripts]`` section, so building/installing the wheel produces
   NO ``seam`` console-script command. The ``[project]`` block (L5-11) declares
   only ``name``/``version``/``requires-python``/``dependencies``.

2. Wheel resources are not package data: the resources the installed package
   must ship (``workflows/``, ``prompts/``, ``config/``, ``schemas/``,
   ``test_project_template/``) are loaded via ABSOLUTE source-tree paths —
   ``core/paths.py:14-16`` ``src_root()`` = ``Path(__file__).resolve().parent.parent``,
   ``core/prompt_loader.py:38`` ``prompts_dir`` = ``.../prompts``,
   ``core/config_loader.py:11-12`` ``_DEFAULT_CONFIG`` = ``.../config/framework_defaults.yaml``,
   ``tests/e2e/e2e_v3_bootstrap.py:9-10`` ``PACKAGE_ROOT`` = ``Path(__file__).resolve().parents[2]`` —
   NOT via ``importlib.resources`` (grep: zero ``importlib.resources`` uses in
   ``core/``). ``pyproject.toml`` has neither ``[tool.setuptools] package-data``
   nor ``include-package-data``, so a built wheel contains only ``.py`` modules
   (per ``packages.find`` include L28-38) and none of the resource directories
   — they are not resolvable at install time through
   ``importlib.resources.files(<package>)``.

3. No ``seam`` entry point: even though ``scripts/sm_adapt_cli.py`` defines a
   callable ``main()`` (L144-173, currently a dead stub returning 0), without a
   ``[project.scripts]`` declaration the installed wheel exposes no ``seam``
   command. The ``main()`` callable alone is not enough — the entry point must
   be declared in ``pyproject.toml``.

RED semantics: these tests must FAIL against current code with AssertionError
for the reasons above (NOT ImportError), and turn GREEN only when T17 adds the
``[project.scripts]`` ``seam`` entry plus package-data/``importlib.resources``
wiring. No source file is modified here; ``tests/test_ci_contract.py:43-51``
``EXPECTED_PACKAGES`` is read-only and stays untouched.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "src" / "pyproject.toml"
SRC_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = SRC_ROOT / "scripts" / "sm_adapt_cli.py"

# Resource directories the installed wheel must ship for the package to work.
_WHEEL_RESOURCE_DIRS = (
    "workflows",
    "prompts",
    "config",
    "schemas",
    "test_project_template",
)

# Current packages.find include from src/pyproject.toml L28-38 — .py modules
# only; non-code resources are not covered.
_PACKAGES_FIND_INCLUDE = (
    "core*",
    "harness*",
    "migrator*",
    "rule_strategies*",
    "scripts*",
    "validators*",
    "workflows*",
)

# The console-script entry the wheel must expose (bug #10 contract).
_SEAM_ENTRY_POINT = "seam"
_SEAM_ENTRY_TARGET = "scripts.sm_adapt_cli:main"

_MISSING_SCRIPTS_MSG = (
    f"src/pyproject.toml declares NO [project.scripts] section "
    f"(project keys = {{project_keys}}), so the wheel installs no "
    f"`{_SEAM_ENTRY_POINT}` console script. T17 must add "
    f"`[project.scripts] {_SEAM_ENTRY_POINT} = {_SEAM_ENTRY_TARGET!r}`."
)

_MISSING_PACKAGE_DATA_MSG = (
    "src/pyproject.toml declares NO [tool.setuptools] package-data wiring "
    "(include-package-data and package-data both absent), so the built wheel "
    "ships only .py modules per packages.find include and NONE of the resource "
    f"dirs {_WHEEL_RESOURCE_DIRS}. The loaders currently read them via "
    "absolute source-tree paths — paths.py:14-16, prompt_loader.py:38, "
    "config_loader.py:11-12, tests/e2e/e2e_v3_bootstrap.py:9-10 — which breaks "
    "in an installed wheel: importlib.resources.files(<pkg>).joinpath(...) "
    "cannot resolve them. T17 must add include-package-data/package-data "
    "(plus MANIFEST.in for the data-only dirs) so the loaders can switch to "
    "importlib.resources."
)


def _load_pyproject() -> dict:
    """Parse src/pyproject.toml with stdlib tomllib (3.11+)."""
    assert PYPROJECT_PATH.is_file(), f"pyproject missing: {PYPROJECT_PATH}"
    with PYPROJECT_PATH.open("rb") as stream:
        return tomllib.load(stream)


class TestConsoleScriptEntry:
    """Divergence 1 — [project.scripts] section absent from pyproject.toml."""

    def test_pyproject_declares_seam_console_script(self) -> None:
        # Given the current packaging metadata.
        project = _load_pyproject().get("project", {})
        project_keys = ", ".join(sorted(project))

        # When the console-script registry is inspected.
        scripts = project.get("scripts")

        # Then the wheel must install a `seam` console script.
        assert scripts is not None, _MISSING_SCRIPTS_MSG.format(
            project_keys=project_keys
        )
        assert (
            _SEAM_ENTRY_POINT in scripts
        ), f"[project.scripts] exists but has no `{_SEAM_ENTRY_POINT}` key: {scripts!r}"


class TestPackageResourcesDeclaredAsPackageData:
    """Divergence 2 — resources are not package data, so an installed wheel
    cannot resolve them via importlib.resources."""

    def test_resource_dirs_declared_as_wheel_package_data(self) -> None:
        # Given the current packaging metadata.
        data = _load_pyproject()
        setuptools = data.get("tool", {}).get("setuptools", {})
        include_package_data = setuptools.get("include-package-data")
        package_data = setuptools.get("package-data")

        # When the setuptools package-data wiring is inspected.
        # Then the wheel must ship the resource directories as package data so
        # importlib.resources.files(<pkg>).joinpath(<resource>).is_dir() works
        # after installation.
        assert (
            include_package_data is True or bool(package_data)
        ), _MISSING_PACKAGE_DATA_MSG

        # Post-GREEN validation (runs once T17 adds the wiring above): every
        # dir that is an importable package must be traversable; data-only dirs
        # must be covered by the declared package-data/MANIFEST.
        for pkg_dir in _WHEEL_RESOURCE_DIRS:
            if pkg_dir not in _PACKAGES_FIND_INCLUDE:
                continue  # data-only dir — covered by include-package-data/MANIFEST
            traversable = importlib.resources.files(pkg_dir)
            assert traversable.is_dir(), (
                f"package-data wired but importlib.resources.files({pkg_dir!r}) "
                f"does not resolve to a directory: {traversable}"
            )


class TestSeamEntryPoint:
    """Divergence 3 — sm_adapt_cli.main() exists but no seam entry point is
    declared, so the installed package exposes no `seam` command."""

    def test_seam_entry_point_declared_and_target_resolvable(self) -> None:
        # Given the CLI module already defines a callable entry function.
        module_name, _, attr_name = _SEAM_ENTRY_TARGET.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr_name)), (
            f"{_SEAM_ENTRY_TARGET} must be callable — current "
            f"{module_name}.{attr_name} = {getattr(module, attr_name)!r}"
        )
        # Control: main() exists at sm_adapt_cli.py L144 (dead stub) and is
        # importable — the gap is purely the missing declaration.

        # When the console-script declaration is inspected.
        scripts = _load_pyproject().get("project", {}).get("scripts")

        # Then the wheel must declare the seam entry pointing at that target;
        # without it, `seam` is never installed as a command.
        assert scripts is not None, (
            f"`{_SEAM_ENTRY_POINT}` entry point is undeclared: src/pyproject.toml "
            f"has no [project.scripts], so even though {_SEAM_ENTRY_TARGET} is "
            "importable and callable, the installed wheel exposes no `seam` "
            "console script. T17 must declare "
            f"[project.scripts] {_SEAM_ENTRY_POINT} = {_SEAM_ENTRY_TARGET!r}."
        )
        assert scripts.get(_SEAM_ENTRY_POINT) == _SEAM_ENTRY_TARGET, (
            f"[project.scripts] must map `{_SEAM_ENTRY_POINT}` to "
            f"{_SEAM_ENTRY_TARGET!r}, got: {scripts!r}"
        )
