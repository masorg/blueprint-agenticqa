"""Agentic QA - a testing-pyramid QA app for Cloudera CML/CAI.

See docs/09-agentic-qa-app-spec.md for the architecture. This package is the
backend: it runs a target repo's tests layer-by-layer (the "pyramid"), streams
live results to a dashboard, and gates a PR on human review when needed.
"""

__version__ = "0.1.0"
