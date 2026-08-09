"""Package marker — ships ``config/*.yaml`` defaults as wheel package data.

Data-only package (no Python modules); declared in ``pyproject.toml``
``[tool.setuptools] package-data`` so ``importlib.resources`` can resolve the
config files both in the source tree and in an installed wheel.
"""
