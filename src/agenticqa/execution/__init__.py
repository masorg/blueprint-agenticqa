"""Execution/isolation layer: check out untrusted PR code and run it safely.

The `Executor`/`Workspace` interfaces are the seam that lets a stronger backend
(CML Job/Worker, container) replace the local subprocess+venv implementation
without touching the pipeline above.
"""
