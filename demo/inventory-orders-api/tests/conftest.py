"""Shared fixtures for the Inventory & Orders test suites."""

import pytest

import app as demo_app


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the in-memory store around every test so cases are independent.

    Property tests additionally reset inside each Hypothesis example (a single
    test function runs many examples without re-triggering this fixture).
    """
    demo_app.reset_state()
    yield
    demo_app.reset_state()
