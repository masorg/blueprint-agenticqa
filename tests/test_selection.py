"""Change-based test selection: the deterministic core, fail-closed fallbacks, and add-only advice."""

from __future__ import annotations

from pathlib import Path

from agenticqa.models.pyramid import DiscoveredTest
from agenticqa.selection import (
    RepoGraph,
    parse_diff,
    select_tests,
    static_tests,
    with_advice,
)


def _diff(*files: tuple[str, str]) -> str:
    """A minimal git-style diff touching the given (path, status) files."""
    out = []
    for path, status in files:
        out.append(f"diff --git a/{path} b/{path}")
        if status == "added":
            out.append("new file mode 100644")
        if status == "deleted":
            out.append("deleted file mode 100644")
        out.append(f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y")
    return "\n".join(out) + "\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _tests(root: Path) -> list[DiscoveredTest]:
    return static_tests(RepoGraph(root))


BASE = {
    "src/pkg/__init__.py": "",
    "src/pkg/a.py": "def f():\n    return 1\n",
    "src/pkg/b.py": "from pkg import c\n\ndef g():\n    return c.h()\n",
    "src/pkg/c.py": "def h():\n    return 2\n",
    "src/pkg/lonely.py": "def z():\n    return 3\n",
    "tests/test_a.py": "from pkg.a import f\n\ndef test_f():\n    assert f() == 1\n",
    "tests/test_b.py": "from pkg.b import g\n\ndef test_g():\n    assert g() == 2\n",
}


def test_change_selects_only_tests_that_reach_the_file(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root))
    assert sel.mode == "change"
    assert sel.selected == ["tests/test_a.py::test_f"]
    assert sel.uncovered == []


def test_selection_follows_imports_transitively(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("src/pkg/c.py", "modified")), _tests(root))
    assert sel.selected == ["tests/test_b.py::test_g"]


def test_a_change_no_test_reaches_is_reported_uncovered(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert sel.mode == "change" and sel.selected == []
    assert sel.uncovered == ["src/pkg/lonely.py"]


def test_documentation_only_change_is_inert_not_uncovered(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("README.md", "modified"), ("docs/guide.rst", "modified")), _tests(root))
    assert sel.selected == [] and sel.uncovered == [] and sel.inert == ["README.md", "docs/guide.rst"]


def test_changed_test_file_selects_its_tests(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("tests/test_b.py", "modified")), _tests(root))
    assert sel.selected == ["tests/test_b.py::test_g"]


def test_conftest_change_selects_tests_below_it(tmp_path):
    root = _repo(tmp_path, {**BASE, "tests/conftest.py": "import pytest\n"})
    sel = select_tests(root, _diff(("tests/conftest.py", "modified")), _tests(root))
    assert set(sel.selected) == {"tests/test_a.py::test_f", "tests/test_b.py::test_g"}


def test_build_or_test_configuration_change_runs_everything(tmp_path):
    root = _repo(tmp_path, BASE)
    for path in ("pyproject.toml", "requirements-dev.txt", "pytest.ini"):
        sel = select_tests(root, _diff((path, "modified")), _tests(root))
        assert sel.mode == "full" and len(sel.selected) == 2, path


def test_unreadable_or_empty_diff_runs_everything(tmp_path):
    root = _repo(tmp_path, BASE)
    for diff in ("", "not a diff at all"):
        sel = select_tests(root, diff, _tests(root))
        assert sel.mode == "full" and len(sel.selected) == 2


def test_analysis_error_fails_closed(tmp_path, monkeypatch):
    root = _repo(tmp_path, BASE)
    tests = _tests(root)

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr("agenticqa.selection.RepoGraph", boom)
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), tests)
    assert sel.mode == "full" and len(sel.selected) == 2 and "analysis failed" in sel.reason


def test_deleted_module_selects_the_tests_that_imported_it(tmp_path):
    files = {k: v for k, v in BASE.items() if k != "src/pkg/a.py"}  # a.py is gone in the changed tree
    root = _repo(tmp_path, files)
    sel = select_tests(root, _diff(("src/pkg/a.py", "deleted")), _tests(root))
    assert "tests/test_a.py::test_f" in sel.selected


def test_relative_imports_and_package_inits_are_followed(tmp_path):
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "from .core import run\n",
        "lib/pkg/core.py": "from . import util\n\ndef run():\n    return util.u()\n",
        "lib/pkg/util.py": "def u():\n    return 1\n",
        "tests/test_pkg.py": "import pkg\n\ndef test_run():\n    assert pkg.run() == 1\n",
    })
    sel = select_tests(root, _diff(("lib/pkg/util.py", "modified")), _tests(root))
    assert sel.selected == ["tests/test_pkg.py::test_run"]


def test_dynamic_loading_makes_a_test_depend_on_any_source_change(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "tests/test_plugins.py": "import importlib\n\nNAME = 'pkg.' + 'lonely'\n\ndef test_load():\n    importlib.import_module(NAME)\n",
    })
    sel = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert "tests/test_plugins.py::test_load" in sel.selected and sel.uncovered == []


def test_data_file_read_by_name_selects_the_reading_test(tmp_path):
    root = _repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "tests/test_cfg.py": "def test_x():\n    open('settings.json')\n",
        "settings.json": "{}",
    })
    sel = select_tests(root, _diff(("settings.json", "modified")), _tests(root))
    assert sel.selected == ["tests/test_cfg.py::test_x"]


def test_advice_can_only_add_tests(tmp_path):
    root = _repo(tmp_path, BASE)
    tests = _tests(root)
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), tests)
    core = list(sel.core)
    known = {t.node_id for t in tests}
    sel = with_advice(sel, ["tests/test_b.py::test_g", "tests/nope.py::test_ghost", "tests/test_a.py::test_f"], known)
    assert sel.core == core
    assert set(core) <= set(sel.selected)
    assert sel.added == ["tests/test_b.py::test_g"]
    assert "tests/nope.py::test_ghost" not in sel.selected


def test_empty_or_hostile_advice_never_shrinks_the_core(tmp_path):
    root = _repo(tmp_path, BASE)
    tests = _tests(root)
    known = {t.node_id for t in tests}
    for advice in ([], ["x"] * 5, [t.node_id for t in tests]):
        sel = select_tests(root, _diff(("src/pkg/c.py", "modified")), tests)
        core = set(sel.core)
        sel = with_advice(sel, advice, known)
        assert core <= set(sel.selected)


def test_parse_diff_reads_git_and_plain_unified_formats():
    assert [c.path for c in parse_diff(_diff(("a/b.py", "modified"), ("c.md", "added")))] == ["a/b.py", "c.md"]
    plain = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n"
    assert [c.path for c in parse_diff(plain)] == ["x.py"]
    assert parse_diff("") is None


# --------------------------------------------------------------------------- #
# Name level: a package that re-exports everything makes every test reach every file;
# the refinement selects by the functions a change affects.
# --------------------------------------------------------------------------- #
import difflib


def _real_diff(path: str, old: str, new: str) -> str:
    body = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))
    return f"diff --git a/{path} b/{path}\n{body}"


LIB_OLD = '''"""lib"""
from . import util

LIMIT = 10


def parse(x):
    return util.clean(x) + 1


def render(x):
    return str(x)


class Box:
    def __init__(self, v):
        self.v = v

    def size(self):
        return len(self.v)
'''
UTIL_OLD = "def clean(x):\n    return x\n"
NAMED = {
    "lib/pkg/__init__.py": "from .core import parse, render, Box\n",
    "lib/pkg/util.py": UTIL_OLD,
    "tests/test_parse.py": "import pkg\n\ndef test_parse():\n    assert pkg.parse(1) == 2\n",
    "tests/test_render.py": "import pkg\n\ndef test_render():\n    assert pkg.render(1) == '1'\n",
    "tests/test_box.py": "import pkg\n\ndef test_box():\n    assert pkg.Box([1]).size() == 1\n",
}


def _named_repo(tmp_path, lib=LIB_OLD):
    return _repo(tmp_path, {**NAMED, "lib/pkg/core.py": lib})


def _sel(root, new_lib, level="name", util=None):
    old_files = {"lib/pkg/core.py": LIB_OLD}
    diff = _real_diff("lib/pkg/core.py", LIB_OLD, new_lib)
    (root / "lib/pkg/core.py").write_text(new_lib)
    return select_tests(root, diff, _tests(root), level=level)


def test_file_level_selects_every_test_that_reaches_the_file(tmp_path):
    root = _named_repo(tmp_path)
    sel = _sel(root, LIB_OLD.replace("return str(x)", "return repr(x)"), level="file")
    assert len(sel.selected) == 3


def test_name_level_selects_only_tests_that_mention_the_changed_function(tmp_path):
    root = _named_repo(tmp_path)
    sel = _sel(root, LIB_OLD.replace("return str(x)", "return repr(x)"))
    assert sel.selected == ["tests/test_render.py::test_render"] and sel.uncovered == []


def test_name_level_follows_callers_through_the_library(tmp_path):
    root = _named_repo(tmp_path)
    new_util = "def clean(x):\n    return x + 0\n"
    diff = _real_diff("lib/pkg/util.py", UTIL_OLD, new_util)
    (root / "lib/pkg/util.py").write_text(new_util)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_parse.py::test_parse"]  # parse() calls clean()


def test_a_changed_method_selects_tests_that_call_it_by_name(tmp_path):
    root = _named_repo(tmp_path)
    sel = _sel(root, LIB_OLD.replace("return len(self.v)", "return len(self.v) + 0"))
    assert sel.selected == ["tests/test_box.py::test_box"]


def test_a_changed_constructor_selects_tests_that_use_the_class(tmp_path):
    root = _named_repo(tmp_path)
    sel = _sel(root, LIB_OLD.replace("self.v = v", "self.v = list(v)"))
    assert sel.selected == ["tests/test_box.py::test_box"]


def test_a_changed_literal_constant_affects_the_code_that_mentions_it(tmp_path):
    lib = LIB_OLD.replace("return str(x)", "return str(x)[:LIMIT]")
    root = _named_repo(tmp_path, lib)
    new = lib.replace("LIMIT = 10", "LIMIT = 11")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_render.py::test_render"]  # render() mentions LIMIT


def test_module_level_code_that_runs_stays_file_level(tmp_path):
    lib = LIB_OLD.replace("LIMIT = 10", "LIMIT = int('10')")
    root = _named_repo(tmp_path, lib)
    new = lib.replace("int('10')", "int('11')")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert len(sel.selected) == 3  # a computed value can raise at import and break every importer


def test_registered_function_stays_file_level(tmp_path):
    lib = LIB_OLD.replace("def render(x):", "@registry.add('r')\ndef render(x):").replace(
        "from . import util", "from . import util\nfrom .reg import registry")
    root = _repo(tmp_path, {**NAMED, "lib/pkg/core.py": lib, "lib/pkg/reg.py": "class _R:\n    def add(self, k):\n        return lambda f: f\nregistry = _R()\n"})
    new = lib.replace("return str(x)", "return repr(x)")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert len(sel.selected) == 3


def test_comment_only_change_is_inert(tmp_path):
    root = _named_repo(tmp_path)
    sel = _sel(root, LIB_OLD.replace("def render(x):", "# render a value\ndef render(x):"))
    assert sel.selected == [] and sel.uncovered == [] and sel.inert == ["lib/pkg/core.py"]


def test_a_changed_function_no_test_mentions_is_reported_uncovered(tmp_path):
    lib = LIB_OLD + "\n\ndef unused(x):\n    return x\n"
    root = _named_repo(tmp_path, lib)
    new = lib.replace("def unused(x):\n    return x", "def unused(x):\n    return x + 1")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == [] and sel.uncovered == ["lib/pkg/core.py"]


def test_star_import_in_a_test_makes_it_depend_on_the_whole_file(tmp_path):
    root = _repo(tmp_path, {
        **NAMED, "lib/pkg/core.py": LIB_OLD,
        "tests/test_star.py": "from pkg.core import *\n\ndef test_all():\n    assert True\n",
    })
    sel = _sel(root, LIB_OLD.replace("return str(x)", "return repr(x)"))
    assert "tests/test_star.py::test_all" in sel.selected


def test_a_literal_dynamic_import_is_an_ordinary_import(tmp_path):
    root = _repo(tmp_path, {**BASE, "tests/test_lit.py": "import importlib\n\ndef test_load():\n    importlib.import_module('pkg.a')\n"})
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root), level="file")
    assert "tests/test_lit.py::test_load" in sel.selected


from hypothesis import given, settings
from hypothesis import strategies as hst


@settings(max_examples=200, deadline=None)
@given(hst.lists(hst.one_of(hst.text(max_size=30), hst.sampled_from(
    ["tests/test_a.py::test_f", "tests/test_b.py::test_g", "tests/nope.py::x"])), max_size=40))
def test_property_no_advice_can_remove_a_core_test_or_invent_one(advice):
    import tempfile

    root = _repo(Path(tempfile.mkdtemp()), BASE)
    tests = _tests(root)
    known = {t.node_id for t in tests}
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), tests)
    core = set(sel.core)
    sel = with_advice(sel, advice, known)
    assert core <= set(sel.selected) <= known
    assert len(sel.selected) == len(set(sel.selected))


# --------------------------------------------------------------------------- #
# Test level: one decision per test function, never by less than what runs with it
# --------------------------------------------------------------------------- #
FN_LIB = "def f():\n    return 1\n\n\ndef g():\n    return 2\n\n\ndef h():\n    return f() + 1\n"


def _fn_repo(tmp_path, tests: dict[str, str]):
    return _repo(tmp_path, {"lib/pkg/__init__.py": "from .core import f, g, h\n", "lib/pkg/core.py": FN_LIB, **tests})


def _change_core(root, old="return 1", new="return 10"):
    new_lib = FN_LIB.replace(old, new)
    diff = _real_diff("lib/pkg/core.py", FN_LIB, new_lib)
    (root / "lib/pkg/core.py").write_text(new_lib)
    return diff


def test_two_tests_in_one_file_are_decided_separately(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_f():\n    assert pkg.f() == 1\n\ndef test_g():\n    assert pkg.g() == 2\n"})
    sel = select_tests(root, _change_core(root), _tests(root))
    assert sel.selected == ["tests/test_x.py::test_f"]
    by_file = select_tests(root, _change_core(root), _tests(root), granularity="file")
    assert len(by_file.selected) == 2


def test_a_test_that_reaches_the_change_through_a_library_caller_is_selected(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_h():\n    assert pkg.h() == 2\n\ndef test_g():\n    assert pkg.g() == 2\n"})
    sel = select_tests(root, _change_core(root), _tests(root))
    assert sel.selected == ["tests/test_x.py::test_h"]  # h() calls f()


def test_a_fixture_that_calls_the_changed_function_selects_the_tests_that_use_it(tmp_path):
    root = _fn_repo(tmp_path, {
        "tests/conftest.py": "import pytest\nimport pkg\n\n@pytest.fixture\ndef value():\n    return pkg.f()\n",
        "tests/test_x.py": "def test_uses(value):\n    assert value\n\ndef test_plain():\n    assert True\n",
    })
    sel = select_tests(root, _change_core(root), _tests(root))
    assert sel.selected == ["tests/test_x.py::test_uses"]


def test_autouse_fixture_and_hooks_apply_to_every_test(tmp_path):
    root = _fn_repo(tmp_path, {
        "tests/conftest.py": "import pytest\nimport pkg\n\n@pytest.fixture(autouse=True)\ndef setup_all():\n    pkg.f()\n",
        "tests/test_x.py": "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n",
    })
    sel = select_tests(root, _change_core(root), _tests(root))
    assert len(sel.selected) == 2


def test_class_setup_is_shared_by_the_tests_of_the_class(tmp_path):
    root = _fn_repo(tmp_path, {
        "tests/test_x.py": "import pkg\n\nclass TestBox:\n    def setup_method(self):\n        self.v = pkg.f()\n\n"
                           "    def test_one(self):\n        assert True\n\n    def test_two(self):\n        assert True\n\n"
                           "def test_outside():\n    assert True\n",
    })
    sel = select_tests(root, _change_core(root), _tests(root))
    assert set(sel.selected) == {"tests/test_x.py::TestBox::test_one", "tests/test_x.py::TestBox::test_two"}


def test_module_level_test_code_is_shared_by_every_test_in_the_file(tmp_path):
    root = _fn_repo(tmp_path, {
        "tests/test_x.py": "import pkg\n\nCASES = [pkg.f()]\n\ndef test_a():\n    assert True\n\ndef test_b():\n    assert True\n",
    })
    sel = select_tests(root, _change_core(root), _tests(root))
    assert len(sel.selected) == 2


def test_a_modified_test_function_selects_only_that_test(tmp_path):
    old = "import pkg\n\ndef test_f():\n    assert pkg.f() == 1\n\ndef test_g():\n    assert pkg.g() == 2\n"
    new = old.replace("pkg.g() == 2", "pkg.g() == 2  # same\n    assert True")
    root = _fn_repo(tmp_path, {"tests/test_x.py": new})
    diff = _real_diff("tests/test_x.py", old, new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_x.py::test_g"]


def test_a_class_with_a_base_from_another_file_falls_back_to_the_whole_file(tmp_path):
    root = _fn_repo(tmp_path, {
        "tests/base.py": "import pkg\n\nclass Base:\n    def setup_method(self):\n        pkg.f()\n",
        "tests/test_x.py": "from base import Base\n\nclass TestX(Base):\n    def test_a(self):\n        assert True\n",
    })
    sel = select_tests(root, _change_core(root), _tests(root))
    assert sel.selected  # not pruned away: the base class's set-up reaches the changed function


def test_a_test_that_starts_a_process_depends_on_any_source_change(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "src/pkg/__main__.py": "print('hi')\n",
        "tests/test_cli.py": "import subprocess, sys\n\ndef test_cli():\n    subprocess.run([sys.executable, '-m', 'pkg'])\n",
    })
    sel = select_tests(root, _diff(("src/pkg/__main__.py", "modified")), _tests(root))
    assert "tests/test_cli.py::test_cli" in sel.selected and sel.uncovered == []


def test_a_module_that_registers_itself_by_name_stays_file_level(tmp_path):
    lib = "import codecs\n\ndef search(name):\n    return None\n\ndef encode(x):\n    return x\n\ncodecs.register(search)\n"
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "from . import codec\n", "lib/pkg/codec.py": lib,
        "tests/test_codec.py": "import pkg\n\ndef test_roundtrip():\n    assert 'a'.encode('x') == b'a'\n",
    })
    new = lib.replace("return x", "return x + ''")
    diff = _real_diff("lib/pkg/codec.py", lib, new)
    (root / "lib/pkg/codec.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_codec.py::test_roundtrip"]


# --------------------------------------------------------------------------- #
# Computed imports and started processes are bounded by the literals that could name the target
# --------------------------------------------------------------------------- #
def test_a_computed_import_without_any_matching_literal_does_not_taint_every_test(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "src/pkg/loader.py": "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n",
        "tests/test_loader.py": "from pkg.loader import load\n\ndef test_load():\n    assert load('json')\n",
    })
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root))
    assert sel.selected == ["tests/test_a.py::test_f"]  # test_loader never names pkg.a


def test_a_computed_import_reaches_the_module_a_test_names_in_a_string(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "src/pkg/loader.py": "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n",
        "tests/test_loader.py": "from pkg.loader import load\n\ndef test_load():\n    assert load('pkg.a')\n",
    })
    sel = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root), level="name")
    assert "tests/test_loader.py::test_load" in sel.selected


def test_a_relative_computed_import_reaches_the_siblings_of_the_importing_file(tmp_path):
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "import importlib\n\ndef __getattr__(name):\n    return importlib.import_module(f'.{name}', __package__)\n",
        "lib/pkg/extra.py": "def e():\n    return 1\n",
        "lib/other/__init__.py": "",
        "lib/other/far.py": "def f():\n    return 1\n",
        "tests/test_pkg.py": "import pkg\n\ndef test_attr():\n    assert pkg.extra\n",
    })
    near = select_tests(root, _diff(("lib/pkg/extra.py", "modified")), _tests(root))
    far = select_tests(root, _diff(("lib/other/far.py", "modified")), _tests(root))
    assert "tests/test_pkg.py::test_attr" in near.selected
    assert far.selected == [] and far.uncovered == ["lib/other/far.py"]


def test_a_started_process_reaches_the_module_it_runs_by_name(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "src/pkg/__main__.py": "from pkg import a\nprint(a.f())\n",
        "tests/test_cli.py": "import subprocess, sys\n\ndef test_cli():\n    subprocess.run([sys.executable, '-m', 'pkg'])\n",
    })
    deep = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root))
    assert "tests/test_cli.py::test_cli" in deep.selected  # pkg/__main__ imports pkg.a
    other = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert "tests/test_cli.py::test_cli" not in other.selected


def test_a_path_based_loader_is_bounded_by_the_paths_it_names(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "tests/test_path.py": "import importlib.util\n\ndef test_x():\n    importlib.util.spec_from_file_location('m', 'src/pkg/lonely.py')\n",
    })
    named = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert "tests/test_path.py::test_x" in named.selected
    other = select_tests(root, _diff(("src/pkg/a.py", "modified")), _tests(root))
    assert "tests/test_path.py::test_x" not in other.selected


def test_a_file_that_cannot_be_parsed_taints_every_test_that_reaches_it(tmp_path):
    root = _repo(tmp_path, {
        **BASE,
        "src/pkg/broken.py": "def f(:\n",
        "tests/test_broken.py": "import pkg.broken\n\ndef test_x():\n    assert True\n",
    })
    sel = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert "tests/test_broken.py::test_x" in sel.selected


def test_a_function_stored_in_a_registry_dict_stays_file_level(tmp_path):
    lib = "def even(x):\n    return x % 2 == 0\n\n\nTESTS = {'even': even}\n"
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "from .reg import TESTS\n", "lib/pkg/reg.py": lib,
        "tests/test_reg.py": "import pkg\n\ndef test_by_name():\n    assert pkg.TESTS['even'](2)\n",
        "tests/test_other.py": "def test_unrelated():\n    assert True\n",
    })
    new = lib.replace("== 0", "== 1")
    diff = _real_diff("lib/pkg/reg.py", lib, new)
    (root / "lib/pkg/reg.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert "tests/test_reg.py::test_by_name" in sel.selected


def test_a_method_looked_up_with_a_getattr_prefix_stays_file_level(tmp_path):
    lib = ("class V:\n    def visit(self, kind):\n        return getattr(self, 'visit_' + kind)()\n\n"
           "    def visit_name(self):\n        return 1\n")
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "from .v import V\n", "lib/pkg/v.py": lib,
        "tests/test_v.py": "import pkg\n\ndef test_visit():\n    assert pkg.V().visit('name') == 1\n",
    })
    new = lib.replace("return 1", "return 2")
    diff = _real_diff("lib/pkg/v.py", lib, new)
    (root / "lib/pkg/v.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_v.py::test_visit"]


# --------------------------------------------------------------------------- #
# Scope policy: unreached files that a person is not asked to review
# --------------------------------------------------------------------------- #
def test_an_unreached_script_outside_the_shipped_code_is_reported_not_flagged(tmp_path):
    root = _repo(tmp_path, {**BASE, "dev/release.py": "def go():\n    return 1\n"})
    sel = select_tests(root, _diff(("dev/release.py", "modified")), _tests(root))
    assert sel.uncovered == [] and sel.out_of_scope == ["dev/release.py"]


def test_an_unreached_file_of_the_package_is_still_flagged(tmp_path):
    root = _repo(tmp_path, BASE)
    sel = select_tests(root, _diff(("src/pkg/lonely.py", "modified")), _tests(root))
    assert sel.uncovered == ["src/pkg/lonely.py"] and sel.out_of_scope == []


def test_an_unreferenced_data_file_is_out_of_scope_but_a_front_end_source_file_is_flagged(tmp_path):
    root = _repo(tmp_path, {**BASE, "web/app.js": "x", "data/names.csv": "a"})
    sel = select_tests(root, _diff(("data/names.csv", "modified"), ("web/app.js", "modified")), _tests(root))
    assert sel.out_of_scope == ["data/names.csv"] and sel.uncovered == ["web/app.js"]


def test_a_script_that_a_test_reaches_is_not_out_of_scope(tmp_path):
    root = _repo(tmp_path, {
        **BASE, "dev/tool.py": "def go():\n    return 1\n",
        "tests/test_tool.py": "import sys\nsys.path.insert(0, 'dev')\nimport tool\n\ndef test_go():\n    assert tool.go() == 1\n",
    })
    sel = select_tests(root, _diff(("dev/tool.py", "modified")), _tests(root))
    assert "tests/test_tool.py::test_go" in sel.selected and sel.out_of_scope == []


def test_a_docstring_edit_counts_as_a_change_to_its_function(tmp_path):
    lib = 'def f():\n    """Return one."""\n    return 1\n\n\ndef g():\n    return 2\n'
    files = {"lib/pkg/__init__.py": "from .m import f, g\n", "lib/pkg/m.py": lib,
             "tests/test_m.py": "import pkg\n\ndef test_f():\n    assert pkg.f() == 1\n\ndef test_g():\n    assert pkg.g() == 2\n"}
    root = _repo(tmp_path, files)
    new = lib.replace("Return one.", "Return the number one.")
    diff = _real_diff("lib/pkg/m.py", lib, new)
    (root / "lib/pkg/m.py").write_text(new)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_m.py::test_f"] and sel.inert == []  # command-line help reads docstrings


def test_a_package_is_importable_by_its_directory_name_even_when_src_is_a_package(tmp_path):
    root = _repo(tmp_path, {
        "src/__init__.py": "",
        "src/pkg/__init__.py": "from .core import f\n",
        "src/pkg/core.py": "def f():\n    return 1\n",
        "tests/test_core.py": "from pkg import f\n\ndef test_f():\n    assert f() == 1\n",
    })
    new = "def f():\n    return 2\n"
    diff = _real_diff("src/pkg/core.py", "def f():\n    return 1\n", new)
    (root / "src/pkg/core.py").write_text(new)
    for level in ("file", "name"):
        sel = select_tests(root, diff, _tests(root), level=level)
        assert sel.selected == ["tests/test_core.py::test_f"], level


def test_a_deleted_code_line_between_comments_is_not_mistaken_for_no_change(tmp_path):
    old = ("class V:\n    def state(self):\n        s = {}\n\n        # keep the dpi\n        s['dpi'] = 1\n\n"
           "        # add the version\n        s['v'] = 2\n        return s\n")
    new = old.replace("        # keep the dpi\n        s['dpi'] = 1\n\n", "")
    root = _repo(tmp_path, {
        "lib/pkg/__init__.py": "from .v import V\n", "lib/pkg/v.py": new,
        "tests/test_v.py": "import pkg\n\ndef test_state():\n    assert pkg.V().state()\n",
    })
    diff = _real_diff("lib/pkg/v.py", old, new)  # the change deletes a code line (and its comment)
    sel = select_tests(root, diff, _tests(root))
    assert sel.selected == ["tests/test_v.py::test_state"] and sel.inert == []


# --------------------------------------------------------------------------- #
# Dynamic core: a recorded coverage map narrows changes inside function bodies
# --------------------------------------------------------------------------- #
from agenticqa.selection import CoverageMap


def _cov(root, mapping: dict[tuple, list[str]]):
    g = RepoGraph(root)
    return CoverageMap({k: set(v) for k, v in mapping.items()}, {k[0] for k in mapping}), g


def test_coverage_map_selects_only_tests_that_executed_the_changed_body(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_f():\n    assert pkg.f() == 1\n\ndef test_h():\n    assert pkg.h() == 2\n"})
    diff = _change_core(root)
    cov, g = _cov(root, {("lib/pkg/core.py", None, "f"): ["tests/test_x.py::test_f"]})
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert sel.selected == ["tests/test_x.py::test_f"]  # statically h() calls f(), but h's test never ran f here


def test_a_body_no_recorded_test_executed_is_unreached(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_g():\n    assert pkg.g() == 2\n"})
    diff = _change_core(root)
    cov, g = _cov(root, {("lib/pkg/core.py", None, "g"): ["tests/test_x.py::test_g"]})
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert sel.selected == [] and sel.uncovered == ["lib/pkg/core.py"]


def test_a_change_outside_function_bodies_keeps_the_static_rules(tmp_path):
    lib = "LIMIT = int('1')\n\ndef f():\n    return LIMIT\n"
    root = _repo(tmp_path, {"lib/pkg/__init__.py": "from .core import f\n", "lib/pkg/core.py": lib,
                            "tests/test_x.py": "import pkg\n\ndef test_a():\n    assert pkg.f()\n\ndef test_b():\n    assert True\n"})
    new = lib.replace("int('1')", "int('2')")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    cov, g = _cov(root, {("lib/pkg/core.py", None, "f"): ["tests/test_x.py::test_a"]})
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert len(sel.selected) == 2  # import-time code: every test that imports the file


def test_without_a_coverage_map_for_the_file_the_static_core_applies(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_h():\n    assert pkg.h() == 2\n"})
    diff = _change_core(root)
    cov, g = _cov(root, {("lib/pkg/other.py", None, "z"): ["tests/test_x.py::test_h"]})
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert sel.selected == ["tests/test_x.py::test_h"]  # static: h() calls f()


def test_a_body_that_ran_outside_any_test_falls_back_to_the_static_rules(tmp_path):
    root = _fn_repo(tmp_path, {"tests/test_x.py": "import pkg\n\ndef test_h():\n    assert pkg.h() == 2\n"})
    diff = _change_core(root)
    g = RepoGraph(root)
    cov = CoverageMap({}, {"lib/pkg/core.py"}, outside={("lib/pkg/core.py", None, "f")})
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert sel.selected == ["tests/test_x.py::test_h"]  # static: h() calls f(); the map cannot vouch for f


def test_a_decorated_function_falls_back_to_the_static_rules(tmp_path):
    lib = "import functools\n\n@functools.lru_cache(maxsize=None)\ndef f():\n    return 1\n\n\ndef h():\n    return f() + 1\n"
    root = _repo(tmp_path, {"lib/pkg/__init__.py": "from .core import f, h\n", "lib/pkg/core.py": lib,
                            "tests/test_x.py": "import pkg\n\ndef test_h():\n    assert pkg.h() == 2\n"})
    new = lib.replace("return 1", "return 10")
    diff = _real_diff("lib/pkg/core.py", lib, new)
    (root / "lib/pkg/core.py").write_text(new)
    g = RepoGraph(root)
    cov = CoverageMap({}, {"lib/pkg/core.py"})  # a cache hit: no test executed f's body in the recorded run
    sel = select_tests(root, diff, _tests(root), graph=g, coverage=cov)
    assert sel.selected == ["tests/test_x.py::test_h"] and sel.uncovered == []


# --------------------------------------------------------------------------- #
# Evidence taken away: removed or weakened tests, and doctest files
# --------------------------------------------------------------------------- #
def test_removing_a_test_function_is_recorded_as_weakened(tmp_path):
    old = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n"
    new = "def test_a():\n    assert 1\n"
    root = _repo(tmp_path, {**BASE, "tests/test_w.py": new})
    sel = select_tests(root, _real_diff("tests/test_w.py", old, new), _tests(root))
    assert any("test_b removed" in w for w in sel.weakened)


def test_adding_a_skip_or_dropping_an_assertion_is_recorded(tmp_path):
    old = "def test_a():\n    assert f() == 1\n    assert g() == 2\n"
    new = "import pytest\n\n@pytest.mark.skip\ndef test_a():\n    assert f() == 1\n"
    root = _repo(tmp_path, {**BASE, "tests/test_w.py": new})
    sel = select_tests(root, _real_diff("tests/test_w.py", old, new), _tests(root))
    assert any("skip" in w for w in sel.weakened) and any("assertion" in w for w in sel.weakened)


def test_deleting_a_test_file_is_recorded_even_when_everything_runs(tmp_path):
    root = _repo(tmp_path, {k: v for k, v in BASE.items() if k != "tests/test_b.py"})
    diff = _diff(("tests/test_b.py", "deleted"), ("pyproject.toml", "modified"))
    sel = select_tests(root, diff, _tests(root))
    assert sel.mode == "full" and any("deleted" in w for w in sel.weakened)


def test_renaming_a_test_function_is_not_weakening(tmp_path):
    old = "def test_a():\n    assert 1\n"
    new = "def test_a():\n    assert 1 == 1\n"
    root = _repo(tmp_path, {**BASE, "tests/test_w.py": new})
    sel = select_tests(root, _real_diff("tests/test_w.py", old, new), _tests(root))
    assert sel.weakened == []


def test_a_documentation_file_that_the_runner_collects_is_a_test(tmp_path):
    root = _repo(tmp_path, {**BASE, "docs/usage.rst": ">>> 1 + 1\n2\n"})
    tests = _tests(root) + [DiscoveredTest(node_id="docs/usage.rst::usage.rst", file="docs/usage.rst", name="usage.rst")]
    sel = select_tests(root, _diff(("docs/usage.rst", "modified")), tests)
    assert sel.selected == ["docs/usage.rst::usage.rst"] and sel.inert == []


def test_a_conftest_hook_that_can_rewrite_results_is_recorded(tmp_path):
    old = "import pytest\n"
    new = "import pytest\n\ndef pytest_runtest_makereport(item, call):\n    pass\n"
    root = _repo(tmp_path, {**BASE, "tests/conftest.py": new})
    sel = select_tests(root, _real_diff("tests/conftest.py", old, new), _tests(root))
    assert any("pytest_runtest_makereport" in w for w in sel.weakened)


def test_a_configuration_option_that_deselects_tests_is_recorded(tmp_path):
    old = "[pytest]\naddopts = -q\n"
    new = "[pytest]\naddopts = -q --deselect tests/test_a.py::test_f\n"
    root = _repo(tmp_path, {**BASE, "pytest.ini": new})
    sel = select_tests(root, _real_diff("pytest.ini", old, new), _tests(root))
    assert sel.mode == "full" and any("deselects" in w for w in sel.weakened)
