"""Manifest signal: dependency-name matching, self-reference, and path filtering."""

from agenticqa.eval.manifest_signal import is_manifest, scan_text

PYPROJECT = '''\
[project]
name = "openai-agents"
dependencies = [
  "openai>=1.0",
  "requests",
]
[project.optional-dependencies]
dev = ["pytest", "langchain-core>=0.2"]
# anthropic is mentioned only in a comment
'''


def test_dependency_names_match_including_prefixes_and_extras():
    assert scan_text(PYPROJECT) == {"openai", "langchain"}
    assert scan_text("llama_index_core==0.11\nlitellm[proxy]>=1.0") == {"llama-index", "litellm"}


def test_a_package_does_not_match_itself_via_its_own_name_line():
    assert scan_text('[project]\nname = "smolagents"\ndependencies = ["requests"]') == set()


def test_general_ml_libraries_do_not_count():
    assert scan_text("transformers\ntorch\nnumpy\nhuggingface-hub") == set()


def test_manifest_paths_skip_docs_examples_tests_and_deep_trees():
    assert is_manifest("pyproject.toml") and is_manifest("libs/core/requirements-dev.txt")
    assert not is_manifest("docs/requirements.txt") and not is_manifest("examples/demo/pyproject.toml")
    assert not is_manifest("a/b/c/d/pyproject.toml")
    assert not is_manifest("src/pkg/module.py")
