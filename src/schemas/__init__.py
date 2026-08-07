"""Package marker — ships ``schemas/*.json`` as wheel package data.

Data-only package (no Python modules); declared in ``pyproject.toml``
``[tool.setuptools] package-data`` so ``importlib.resources`` can resolve the
JSON schemas both in the source tree and in an installed wheel.
"""
