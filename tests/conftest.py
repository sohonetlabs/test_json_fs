"""Pytest configuration and shared fixtures."""

import pytest
import logging

# Reduce logging noise during tests
logging.getLogger("jsonfs").setLevel(logging.ERROR)


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (requires FUSE)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )