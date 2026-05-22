"""Test configuration for src."""
import pytest


@pytest.fixture
def base_path():
    """Return the base path for test fixtures."""
    return __file__
