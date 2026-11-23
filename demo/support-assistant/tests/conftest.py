"""Shared fixtures for the Billing Support Assistant suites."""

import pytest

import assistant


@pytest.fixture(autouse=True)
def _reset_state():
    """Restore seed data around every test so cases are independent. Property
    tests additionally reset inside each Hypothesis example."""
    assistant.reset_state()
    yield
    assistant.reset_state()
