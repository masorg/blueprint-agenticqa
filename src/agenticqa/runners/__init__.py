"""Framework runners: discover and execute a target repo's tests.

Layers (unit/property/adversarial/...) are cross-cutting *classifications* of
tests, not runner types. A runner is per test-framework (pytest today; a JS/Go
runner is a new implementation of the same interface).
"""
