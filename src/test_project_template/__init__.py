"""Package marker — ships the ``test_project_template/`` skeleton as wheel data.

Data-only package (the directory is copied into generated projects); declared
in ``pyproject.toml`` ``[tool.setuptools] package-data`` so the template is
shipped in the wheel and resolvable via ``importlib.resources``.
"""
