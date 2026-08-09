"""Package marker — ships ``prompts/*.md`` templates as wheel package data.

Data-only package (no Python modules); declared in ``pyproject.toml``
``[tool.setuptools] package-data`` so ``importlib.resources`` can resolve the
prompt templates both in the source tree and in an installed wheel.
"""
