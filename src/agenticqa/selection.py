"""Change-based test selection: a deterministic core, plus advice that can only add tests.

Given the diff of a change and the project's tests, `select_tests` decides which tests must run.
The core is static and needs no model: a test is selected when a file it can reach through imports
(or through its `conftest.py` files) is changed, when the test file itself is changed, or when a
changed non-Python file is one the test can read. Anything the analysis cannot account for makes
the answer "run every test" (fail closed): an unreadable diff, a changed build or test
configuration file, or an analysis error.

Two properties are the point of this module, and `tests/test_selection.py` checks both:

* *Models can only add.* `Selection.selected` is the core plus the advised tests that exist. No
  advisor output can remove a core test, so a model cannot make a failing change look safe.
* *Silence is not approval.* A changed file that no test can reach is reported in `uncovered`.
  The pipeline sends such a change to a person: nothing was shown about it.

Assumptions (stated, and measured in the evaluation): a test's outcome can differ between the
base and the changed project only if the test reaches a changed file through the static
dependencies computed here; the project's test run is deterministic.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models.pyramid import DiscoveredTest

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox", "build", "dist", ".eggs",
    "site-packages", ".mypy_cache", ".pytest_cache", ".hypothesis", ".ruff_cache",
}
MAX_FILE_BYTES = 2_000_000
MAX_PY_FILES = 30_000

# Changing any of these can change how every test is built or collected, so the answer is "run all".
CONFIG_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "noxfile.py", "pipfile",
    "pipfile.lock", "poetry.lock", "uv.lock", "environment.yml", "manifest.in", ".python-version",
    "pdm.lock", "conda.yml", "conda.yaml",
}
INERT_SUFFIXES = {
    ".md", ".rst", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".pyi", ".gitignore",
}
INERT_NAMES = {
    "license", "licence", "authors", "codeowners", "changelog", "changes", "history", "news", "readme",
    ".gitignore", ".gitattributes", ".editorconfig", "notice", "contributing", "citation.cff",
}
# A construct through which a module can be loaded without a static import statement.
# Scope policy. A changed file that no test reaches is normally sent to a person, because nothing shows that it is
# safe. Two kinds of unreached file are out of scope by default and are reported without being flagged: files
# under directories that do not ship with the project (documentation, examples, scripts, developer tooling,
# benchmarks), and plain data files that no Python code mentions by name. An organisation can change both lists.
NON_SHIPPING_DIRS = {
    "docs", "doc", "examples", "example", "benchmarks", "benchmark", "bench", "scripts", "script", "tools", "dev",
    "tutorials", "tutorial", "notebooks", "samples", "sample", "demo", "demos", ".github", "ci", ".circleci",
}
DATA_SUFFIXES = {
    ".json", ".csv", ".tsv", ".txt", ".svg", ".html", ".htm", ".css", ".xml", ".po", ".pot", ".lock", ".ipynb",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env", ".sql",
}
# A construct through which code runs without a static import: loading by a computed path, and starting
# another process (a test that runs `python -m pkg` executes files that no import statement names).
_DYNAMIC = re.compile(r"spec_from_file_location|\biter_modules\s*\(|\bwalk_packages\s*\(|\brunpy\b|\bload_module\s*\(")
_SPAWN = re.compile(r"\bsubprocess\b|\bos\.system\b|\bPopen\b|\bsys\.executable\b|\bpexpect\b|\bos\.exec|\bos\.spawn")
_LOADERS = {"import_module", "__import__"}


@dataclass(frozen=True)
class Changed:
    path: str
    old_path: str | None = None
    status: str = "modified"  # modified | added | deleted | renamed


def out_of_scope(path: str) -> bool:
    """True for an unreached file that the scope policy does not ask a person to review."""
    parts = path.split("/")
    if any(p.lower() in NON_SHIPPING_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    return kind_of(path) == "other" and Path(name).suffix in DATA_SUFFIXES


@dataclass
class Selection:
    """What to run for one change, and why."""

    mode: str  # "change" (a subset chosen from the diff) | "full" (every test)
    reason: str  # why the full run was chosen, or a short note
    total: int
    core: list[str] = field(default_factory=list)  # node ids chosen by the deterministic core
    added: list[str] = field(default_factory=list)  # node ids added by advice, not in the core
    selected: list[str] = field(default_factory=list)  # what runs: all tests, or core + added
    changed: list[str] = field(default_factory=list)
    inert: list[str] = field(default_factory=list)  # changed files that cannot affect a test
    uncovered: list[str] = field(default_factory=list)  # changed files no test can reach
    out_of_scope: list[str] = field(default_factory=list)  # unreached, but outside the scope policy: reported only
    weakened: list[str] = field(default_factory=list)  # the change removes or weakens tests: evidence taken away

    @property
    def share(self) -> float:
        return (len(self.selected) / self.total) if self.total else 0.0


# --------------------------------------------------------------------------- #
# Reading the diff
# --------------------------------------------------------------------------- #
_HDR = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def parse_diff(diff: str) -> list[Changed] | None:
    """Changed files of a unified diff, or None if the diff cannot be read (the caller then runs all)."""
    if not diff.strip():
        return None
    out: dict[str, Changed] = {}
    lines = diff.splitlines()
    i = 0
    saw_git = any(ln.startswith("diff --git ") for ln in lines)
    if saw_git:
        cur: dict | None = None

        def flush() -> None:
            nonlocal cur
            if cur and cur.get("path"):
                out[cur["path"]] = Changed(cur["path"], cur.get("old"), cur.get("status", "modified"))
            cur = None

        for ln in lines:
            m = _HDR.match(ln)
            if m:
                flush()
                cur = {"old": m.group(1), "path": m.group(2), "status": "modified"}
            elif cur is not None:
                if ln.startswith("new file mode"):
                    cur["status"] = "added"
                elif ln.startswith("deleted file mode"):
                    cur["status"] = "deleted"
                    cur["path"] = cur["old"]
                elif ln.startswith("rename from "):
                    cur["old"] = ln[len("rename from "):]
                    cur["status"] = "renamed"
                elif ln.startswith("rename to "):
                    cur["path"] = ln[len("rename to "):]
        flush()
        return list(out.values()) or None
    # plain `--- a/x` / `+++ b/x` pairs (difflib output, `git diff --no-prefix`)
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = lines[i][4:].split("\t")[0], lines[i + 1][4:].split("\t")[0]
            strip = lambda p: re.sub(r"^[ab]/", "", p)  # noqa: E731
            if new == "/dev/null":
                out[strip(old)] = Changed(strip(old), None, "deleted")
            elif old == "/dev/null":
                out[strip(new)] = Changed(strip(new), None, "added")
            else:
                out[strip(new)] = Changed(strip(new), strip(old) if strip(old) != strip(new) else None,
                                          "renamed" if strip(old) != strip(new) else "modified")
            i += 2
        else:
            i += 1
    return list(out.values()) or None


_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_SKIP_MARK = re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\(|unittest\.skip|@skip\b")
# Hooks and options through which a change can rewrite outcomes or drop tests from a run without deleting them.
_RESULT_HOOK = re.compile(r"^\s*def\s+(pytest_runtest_makereport|pytest_runtest_protocol|pytest_runtest_call|"
                          r"pytest_runtest_setup|pytest_report_teststatus|pytest_collection_modifyitems|"
                          r"pytest_ignore_collect|pytest_pycollect_makeitem|pytest_collect_file|pytest_deselected)\b")
_DESELECT_OPT = re.compile(r"--deselect\b|--ignore(-glob)?\b|(^|\s)-k\s|\bcollect_ignore(_glob)?\b|"
                           r"\bnorecursedirs\b|\b-p\s+no:|\bfilterwarnings\b.*ignore::pytest")
_ASSERT = re.compile(r"^\s*(assert\b|self\.assert\w*\(|pytest\.raises\(|with\s+pytest\.raises)")


def weakened_tests(changed: list["Changed"], fds: dict) -> list[str]:
    """How a change takes evidence away: deleted test files, removed test functions, added skips, fewer assertions.

    A change can pass every test it keeps by removing the test that would fail. Such a change is sent to a person,
    whatever its tests do. Renamed functions count as removed only if no test function of that name is added."""
    found: list[str] = []
    for c in changed:
        path = c.path
        name = path.rsplit("/", 1)[-1]
        fd_any = fds.get(path)
        if fd_any is not None and name.endswith(".py"):
            for t in fd_any.added.values():
                m = _RESULT_HOOK.match(t)
                if m:
                    found.append(f"{path}: hook {m.group(1)} added (can rewrite or drop results)")
        if fd_any is not None and kind_of(path) == "config":
            if any(_DESELECT_OPT.search(t) for t in fd_any.added.values()):
                found.append(f"{path}: option that deselects or ignores tests added")
        is_test = name.startswith("test_") or name.endswith("_test.py") or (
            name.endswith(".py") and any(p in ("test", "tests", "testing") for p in path.split("/")[:-1])
            and name != "conftest.py")
        if not is_test:
            continue
        if c.status == "deleted":
            found.append(f"{path}: test file deleted")
            continue
        fd = fds.get(path)
        if fd is None:
            continue
        removed = {m.group(1) for _, t in fd.removed if (m := _TEST_DEF.match(t))}
        added = {m.group(1) for t in fd.added.values() if (m := _TEST_DEF.match(t))}
        for fn in sorted(removed - added):
            found.append(f"{path}: test {fn} removed")
        if any(_SKIP_MARK.search(t) for t in fd.added.values()) and not any(_SKIP_MARK.search(t) for _, t in fd.removed):
            found.append(f"{path}: skip or xfail added")
        lost = sum(1 for _, t in fd.removed if _ASSERT.match(t)) - sum(1 for t in fd.added.values() if _ASSERT.match(t))
        if lost > 0:
            found.append(f"{path}: {lost} assertion(s) fewer")
    return found


def kind_of(path: str) -> str:
    """python | conftest | config | inert | other."""
    p = Path(path)
    name = p.name.lower()
    if name == "conftest.py":
        return "conftest"
    if name in CONFIG_NAMES or re.match(r"^(requirements|constraints)[^/]*\.(txt|in)$", name):
        return "config"
    if p.suffix == ".py":
        return "python"
    if (p.suffix.lower() in INERT_SUFFIXES or name in INERT_NAMES or name.split(".")[0] in INERT_NAMES
            or path.startswith((".github/", "docs/", "doc/"))):
        return "inert"
    return "other"


# --------------------------------------------------------------------------- #
# Import graph
# --------------------------------------------------------------------------- #
@dataclass
class PyInfo:
    path: str
    package: str | None  # dotted package the file lives in (for relative imports); None if top-level script
    canon: str
    imports: list[tuple[str, int, list[str]]] = field(default_factory=list)  # (module, level, names)
    dynamic: bool = False
    text: str = ""
    names: "NameInfo | None" = None  # None when the file could not be analysed at name level
    strings: set = field(default_factory=set)  # string literals that look like module names, paths, or scripts
    computed_import: bool = False  # imports a module by a computed name
    spawns: bool = False  # starts another process
    prefixes: list = field(default_factory=list)  # literal prefixes of computed import names ("." = relative)
    doctests: bool = False  # the project collects doctests, so a docstring is code


def _literal_prefix(arg) -> str:
    """The literal start of a computed module name, or '' (f"pkg.cmd_{x}", "pkg." + x, f".{x}")."""
    if isinstance(arg, ast.JoinedStr) and arg.values and isinstance(arg.values[0], ast.Constant):
        return str(arg.values[0].value)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add) and isinstance(arg.left, ast.Constant) \
            and isinstance(arg.left.value, str):
        return arg.left.value
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod) and isinstance(arg.left, ast.Constant) \
            and isinstance(arg.left.value, str):
        return arg.left.value.split("%", 1)[0]
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format" \
            and isinstance(arg.func.value, ast.Constant) and isinstance(arg.func.value.value, str):
        return arg.func.value.value.split("{", 1)[0]
    return ""


def _string_literals(tree: ast.AST) -> set[str]:
    """String constants that could name a module, a package, or a script (docstrings and long texts excluded)."""
    skip = _docstring_ids(tree)
    out: set[str] = set()
    for x in ast.walk(tree):
        if isinstance(x, ast.Constant) and isinstance(x.value, str) and id(x) not in skip and len(x.value) < 200:
            v = x.value.strip().strip(".")  # "pkg." and ".mod" are pieces of a module name
            if _IDENT_PATH.fullmatch(v):
                out.add(v)
            elif v.endswith(".py"):
                out.add(Path(v).stem)
    return out


class RepoGraph:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, PyInfo] = {}
        self.keys: dict[str, set[str]] = {}
        self.deps: dict[str, set[str]] = {}
        self.unresolved: dict[str, set[str]] = {}
        self._closure: dict[str, frozenset[str]] = {}
        self._build()

    # ---- construction
    def _py_paths(self) -> list[Path]:
        found: list[Path] = []
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            if (d / "pyvenv.cfg").exists():
                continue
            for e in entries:
                if e.is_dir() and not e.is_symlink():
                    if e.name not in SKIP_DIRS and not e.name.endswith(".egg-info"):
                        stack.append(e)
                elif e.suffix == ".py" and e.is_file():
                    found.append(e)
        if len(found) > MAX_PY_FILES:
            raise RuntimeError(f"too many Python files ({len(found)}) to analyse")
        return sorted(found)

    def _build(self) -> None:
        paths = self._py_paths()
        rel = {p: p.relative_to(self.root).as_posix() for p in paths}
        has_init = {r for r in rel.values() if r.endswith("/__init__.py") or r == "__init__.py"}

        def top_of(path: str) -> tuple[str, list[str]]:
            """The first directory (from the file upward) that is not a package, and the dotted tail."""
            parts = path.split("/")
            stem = parts[-1][:-3]
            comps = [] if stem == "__init__" else [stem]
            d = parts[:-1]
            while d and "/".join(d + ["__init__.py"]) in has_init:
                comps.insert(0, d[-1])
                d = d[:-1]
            return "/".join(d), comps

        for p in paths:
            r = rel[p]
            _, comps = top_of(r)
            canon = ".".join(comps)
            is_init = r.endswith("__init__.py")
            package = ".".join(comps) if is_init else ".".join(comps[:-1])
            info = PyInfo(r, package if comps else None, canon)
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    info.dynamic = True
                    self.files[r] = info
                    continue
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                info.dynamic = True
                self.files[r] = info
                continue
            info.text = src.lower()
            if _DYNAMIC.search(src):  # loads code by a path or by enumerating a package: bounded like a computed import
                info.computed_import = True
                if "iter_modules" in src or "walk_packages" in src:
                    info.prefixes.append(".")
            try:
                tree = ast.parse(src)
            except (SyntaxError, ValueError, RecursionError):
                info.dynamic = True  # a file we cannot read is a file we cannot vouch for
                self.files[r] = info
                if canon:
                    self.keys.setdefault(canon, set()).add(r)
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        info.imports.append((a.name, 0, []))
                elif isinstance(node, ast.ImportFrom):
                    info.imports.append((node.module or "", node.level or 0, [a.name for a in node.names]))
                elif isinstance(node, ast.Call) and _callee(node.func) in _LOADERS:
                    arg = node.args[0] if node.args else None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        info.imports.append((arg.value, 0, []))  # a literal name is an ordinary import
                    else:
                        info.computed_import = True  # bounded by literals, see `extra_reach`
                        prefix = _literal_prefix(arg)
                        if prefix:
                            info.prefixes.append(prefix)
                elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets
                ):
                    for c in ast.walk(node.value):
                        if isinstance(c, ast.Constant) and isinstance(c.value, str):
                            info.imports.append((c.value, 0, []))
            info.names = analyse_names(tree, is_test_side(r))
            info.spawns = bool(_SPAWN.search(src))
            info.strings = _string_literals(tree)
            self.files[r] = info
            # names under which other files can import this one
            path_comps = r[:-3].split("/")
            if path_comps[-1] == "__init__":
                path_comps = path_comps[:-1]
            names = set()
            if canon:
                names.add(canon)
            for i in range(len(path_comps)):
                suffix = path_comps[i:]
                if len(suffix) >= 2:
                    names.add(".".join(suffix))
            if r.endswith("__init__.py") and path_comps:
                names.add(path_comps[-1])  # a package is importable by its directory name (src/ may be a package too)
            for n in names:
                self.keys.setdefault(n, set()).add(r)
        doctests = False
        for cfg in ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py"):
            f = self.root / cfg
            if f.is_file():
                try:
                    doctests = doctests or "doctest" in f.read_text(errors="replace").lower()
                except OSError:
                    pass
        for r, info in self.files.items():
            info.doctests = doctests
            self._resolve_edges(info)

    def _abs(self, info: PyInfo, module: str, level: int) -> str | None:
        if level == 0:
            return module
        if info.package is None:
            return None
        parts = info.package.split(".") if info.package else []
        if level - 1 > len(parts):
            return None
        base = parts[: len(parts) - (level - 1)]
        return ".".join(base + ([module] if module else []))

    def _lookup(self, name: str) -> set[str]:
        out = set(self.keys.get(name, ()))
        parts = name.split(".")
        for i in range(1, len(parts)):  # importing a.b.c also runs the __init__ of a and a.b
            out |= self.keys.get(".".join(parts[:i]), set())
        return out

    def _resolve_edges(self, info: PyInfo) -> None:
        deps: set[str] = set()
        unresolved: set[str] = set()
        for module, level, names in info.imports:
            base = self._abs(info, module, level)
            if base is None:
                continue
            candidates = [base] + [f"{base}.{n}" if base else n for n in names if n != "*"]
            for cand in candidates:
                if not cand:
                    continue
                deps |= self._lookup(cand)
                if cand not in self.keys:  # no file of the project has exactly this name
                    unresolved.add(cand)
        deps.discard(info.path)
        # an imported file's ancestors' `__init__.py` run too
        extra = set()
        for d in deps:
            parts = d.split("/")[:-1]
            while parts:
                init = "/".join(parts + ["__init__.py"])
                if init in self.files:
                    extra.add(init)
                parts = parts[:-1]
        self.deps[info.path] = (deps | extra) - {info.path}
        self.unresolved[info.path] = unresolved

    # ---- queries
    def conftests_for(self, path: str) -> list[str]:
        parts = path.split("/")[:-1]
        out = []
        while True:
            c = "/".join(parts + ["conftest.py"])
            if c in self.files:
                out.append(c)
            if not parts:
                break
            parts = parts[:-1]
        return out

    def closure(self, path: str) -> frozenset[str]:
        """Every file the given file can reach by imports, plus its `conftest.py` chain and their imports."""
        if path in self._closure:
            return self._closure[path]
        seen: set[str] = set()
        stack = [path, *self.conftests_for(path)]
        while stack:
            f = stack.pop()
            if f in seen:
                continue
            seen.add(f)
            stack.extend(self.deps.get(f, ()))
        res = frozenset(seen)
        self._closure[path] = res
        return res

    def last_component(self) -> dict[str, set[str]]:
        """Last dotted component of each file's module name (the package for an `__init__`), to files."""
        if getattr(self, "_last", None) is None:
            idx: dict[str, set[str]] = {}
            for path in self.files:
                comps = path[:-3].split("/")
                if comps[-1] == "__init__":
                    comps = comps[:-1]
                if comps:
                    idx.setdefault(comps[-1], set()).add(path)
            self._last = idx
        return self._last

    def dispatched_by_name(self, names: set[str]) -> bool:
        """True if any of the names is stored as a value at import time or matches a getattr prefix: a lookup by
        string can then run it, and no test needs to mention it."""
        if getattr(self, "_registry", None) is None:
            reg: set[str] = set()
            pre: set[str] = set()
            for path, info in self.files.items():
                if info.names is not None and not is_test_side(path):
                    reg |= info.names.value_refs
                    pre |= info.names.getattr_prefixes
            self._registry, self._prefixes = reg, pre
        return bool(names & self._registry) or any(n.startswith(p) for n in names for p in self._prefixes)

    def callers(self) -> dict[str, set[str]]:
        """name -> names of the library definitions whose body mentions it."""
        if getattr(self, "_callers", None) is None:
            callers: dict[str, set[str]] = {}
            for path, info in self.files.items():
                if info.names is None:
                    continue
                test_side = is_test_side(path)
                for d in info.names.defs:
                    if test_side and d.name.startswith("test"):
                        continue  # a test is a consumer, not something other code calls
                    # a dunder method runs by an implicit call, so the class stands for it (a name like __init__
                    # is mentioned by every subclass and would spread a change to the whole project)
                    dunder = d.name.startswith("__") and d.name.endswith("__")
                    value = (d.owner if dunder else d.name)
                    if not value:
                        continue
                    for r in d.refs:
                        callers.setdefault(r, set()).add(value)
            self._callers = callers
        return self._callers

    def test_refs(self, test_path: str) -> tuple[set[str], bool]:
        """Names mentioned by the test-side files a test reaches, and whether any of them uses `import *`."""
        refs: set[str] = set()
        star = False
        for f in self.closure(test_path):
            info = self.files.get(f)
            if info and info.names is not None and is_test_side(f):
                refs |= info.names.all_refs
                star = star or info.names.star
        return refs, star

    def test_fn_refs(self, test_path: str, key: tuple) -> tuple[set[str], bool]:
        """Names a single test can reach: its own, what its class and module share with it, the autouse
        definitions in scope, and the definitions of the test-side files it imports. Falls back to the whole
        file when the test cannot be found in the syntax tree."""
        info = self.files.get(test_path)
        ni = info.names if info else None
        if ni is None or key not in ni.test_funcs or key[0] in ni.foreign_bases:
            return self.test_refs(test_path)
        refs = set(ni.test_funcs[key]) | ni.shared.get(None, set()) | ni.shared.get(key[0], set())
        star = False
        for f in self.closure(test_path):
            fi = self.files.get(f)
            if fi and fi.names is not None and is_test_side(f):
                refs |= fi.names.autouse
                star = star or fi.names.star
        return refs, star

    def tainted(self, path: str) -> bool:
        """True if the test reaches code that loads modules by a path or that cannot be read: such a test can run
        any file, so it is selected for every source change."""
        return any(self.files[f].dynamic for f in self.closure(path) if f in self.files)

    def extra_reach(self, path: str, spawn_only: bool = False) -> frozenset[str]:
        """Files a test can run through computed imports or started processes, which no import statement shows.

        Assumption (stated in the paper): a module that is loaded by a computed name, or run in a started
        process, is named by a string literal in the dynamic file or in the test-side files that reach it,
        or begins with the literal prefix of the computed name. The entries found this way, and everything
        they import, are reached at file level."""
        if getattr(self, "_extra", None) is None:
            self._extra = {}
        key = (path, spawn_only)
        if key in self._extra:
            return self._extra[key]
        cl = self.closure(path)
        dyn = [f for f in cl if f in self.files and (self.files[f].spawns or
                                                     (self.files[f].computed_import and not spawn_only))]
        if not dyn:
            self._extra[key] = frozenset()
            return self._extra[key]
        literals: set[str] = set()
        for f in cl:
            info = self.files.get(f)
            if info and (f in dyn or is_test_side(f)):
                literals |= info.strings
        entries: set[str] = set()
        for lit in literals:
            entries |= self.keys.get(lit, set())
            if "." not in lit:  # a lone word may be one piece of a name built at run time
                entries |= self.last_component().get(lit, set())
        for f in dyn:
            for pre in self.files[f].prefixes:
                if pre.startswith("."):
                    d = f.rsplit("/", 1)[0] if "/" in f else ""
                    entries |= {g2 for g2 in self.files if g2.rsplit("/", 1)[0] == d} if d else set()
                else:
                    entries |= {m for k, ms in self.keys.items() if k.startswith(pre) for m in ms}
        for e in list(entries):  # `python -m pkg` runs pkg/__main__.py
            if e.endswith("/__init__.py"):
                main = e[: -len("__init__.py")] + "__main__.py"
                if main in self.files:
                    entries.add(main)
        reach: set[str] = set()
        for e in entries:
            reach |= self.closure(e)
        self._extra[key] = frozenset(reach - cl)
        return self._extra[key]

    def keys_of_path(self, path: str) -> set[str]:
        comps = path[:-3].split("/") if path.endswith(".py") else path.split("/")
        if comps and comps[-1] == "__init__":
            comps = comps[:-1]
        out = {".".join(comps[i:]) for i in range(len(comps)) if len(comps[i:]) >= 2}
        if comps:
            out.add(comps[-1])
        return out


def static_tests(graph: RepoGraph) -> list[DiscoveredTest]:
    """Test functions found without running pytest: `test*` functions in `test_*.py` / `*_test.py` files
    (module level, or in a class named `Test*`). Used where no environment can be built."""
    tests: list[DiscoveredTest] = []
    for path, info in sorted(graph.files.items()):
        name = path.rsplit("/", 1)[-1]
        if not (name.startswith("test_") or name.endswith("_test.py")):
            continue
        try:
            tree = ast.parse((graph.root / path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError, RecursionError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                tests.append(DiscoveredTest(node_id=f"{path}::{node.name}", file=path, name=node.name))
            elif isinstance(node, ast.ClassDef) and (
                    node.name.startswith("Test") or node.name.endswith(("Test", "Tests", "TestCase"))
                    or any(_callee(b).endswith(("TestCase", "Test", "Tests")) for b in node.bases)):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                        tests.append(DiscoveredTest(node_id=f"{path}::{node.name}::{sub.name}", file=path,
                                                    name=sub.name))
    return tests


# --------------------------------------------------------------------------- #
# Name-level refinement
# --------------------------------------------------------------------------- #
# File-level reachability is safe and coarse: a package whose `__init__` imports every module makes every
# test reach every file. The refinement below narrows it. A change inside a function body defines a set of
# *affected names* (the function, then every definition that mentions an affected name, and so on). A test
# is selected only if it reaches the changed file AND mentions an affected name. Whatever cannot be
# expressed as a name stays file-level: module-level code, decorators, default values, class bodies,
# registered (decorated) functions, files that fail to parse, and diffs without line information.
_IDENT_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_PLAIN_DECORATORS = {
    "staticmethod", "classmethod", "property", "abstractmethod", "wraps", "lru_cache", "cache", "cached_property",
    "overload", "dataclass", "setter", "getter", "deleter", "total_ordering", "final", "override",
    "contextmanager", "asynccontextmanager",
}


@dataclass
class DefRec:
    name: str
    start: int  # first line, decorators included
    end: int
    body_start: int  # first line of the body
    import_lines: set[int]  # lines that run at definition time: decorators, defaults, bases
    owner: str | None  # the enclosing class, if any
    registered: bool  # carries a decorator that may register the function somewhere
    is_class: bool
    refs: set[str]
    decorated: bool = False  # any decorator: it can cache, wrap, or register the function


@dataclass
class NameInfo:
    defs: list[DefRec] = field(default_factory=list)
    import_refs: set[str] = field(default_factory=set)  # names mentioned by code that runs at import time
    all_refs: set[str] = field(default_factory=set)
    star: bool = False  # `from x import *`: this file's references cannot be listed
    # Test-side files only: what each test function mentions, what its class or module shares with it, and
    # the definitions that apply to every test in scope (autouse fixtures, pytest hooks, module set-up).
    test_funcs: dict = field(default_factory=dict)  # (class or None, function) -> names mentioned
    shared: dict = field(default_factory=dict)  # class or None -> names mentioned by shared code
    autouse: set = field(default_factory=set)
    foreign_bases: set = field(default_factory=set)  # classes whose base lives outside this file
    registers: bool = False  # import-time calls that register things by name (codecs, plugins, signals)
    # Module-level and class-level assignments of literal values: (first line, last line, names assigned).
    # Changing the value cannot raise at import, so it affects only the name and the code that mentions it.
    literal_assigns: list = field(default_factory=list)
    doc_lines: set = field(default_factory=set)  # lines of docstrings
    # Names stored as values at import time ({"even": test_even}) are looked up by a string later, and the
    # literal prefixes of names built for getattr ("visit_" + kind): both dispatch by name.
    value_refs: set = field(default_factory=set)
    getattr_prefixes: set = field(default_factory=set)


def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _refs_of(nodes, skip: set[int]) -> set[str]:
    out: set[str] = set()
    for n in nodes:
        for x in ast.walk(n):
            if isinstance(x, ast.Name):
                out.add(x.id)
            elif isinstance(x, ast.Attribute):
                out.add(x.attr)
            elif isinstance(x, ast.alias):
                out.update(x.name.split("."))
                if x.asname:
                    out.add(x.asname)
            elif isinstance(x, ast.ImportFrom):
                if x.module:
                    out.update(x.module.split("."))
            elif isinstance(x, ast.keyword):
                if x.arg:
                    out.add(x.arg)
            elif isinstance(x, ast.Constant) and isinstance(x.value, str) and id(x) not in skip:
                if len(x.value) < 200 and _IDENT_PATH.fullmatch(x.value):
                    out.update(x.value.split("."))
    return out


def _callee(f: ast.expr) -> str:
    if isinstance(f, ast.Name):
        return f.id
    return f.attr if isinstance(f, ast.Attribute) else ""


def _callees_of(nodes) -> set[str]:
    """Names of the functions that code calls when it runs (decorators count as calls)."""
    out: set[str] = set()
    for n in nodes:
        for x in ast.walk(n):
            if isinstance(x, ast.Call):
                name = _callee(x.func)
                if name:
                    out.add(name)
    return out


def _value_names(nodes) -> set[str]:
    """Names that appear as values (not as the thing being called) in code that runs at import time."""
    called: set[int] = set()
    out: set[str] = set()
    for n in nodes:
        for x in ast.walk(n):
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name):
                called.add(id(x.func))
        for x in ast.walk(n):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and id(x) not in called:
                out.add(x.id)
    return out


def _decorator_name(d: ast.expr) -> str:
    if isinstance(d, ast.Call):
        d = d.func
    if isinstance(d, ast.Attribute):
        return d.attr
    return d.id if isinstance(d, ast.Name) else ""


def _line_span(node: ast.AST) -> set[int]:
    a = getattr(node, "lineno", None)
    return set(range(a, (getattr(node, "end_lineno", a) or a) + 1)) if a else set()


def analyse_names(tree: ast.Module, test_side: bool = False) -> NameInfo:
    info = NameInfo()
    skip = _docstring_ids(tree)
    info.all_refs = _refs_of([tree], skip)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                info.doc_lines |= set(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    info.star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree))

    def visit(body: list[ast.stmt], owner: str | None, import_time: bool) -> None:
        for st_ in body:
            if isinstance(st_, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(st_, ast.ClassDef)
                head_nodes: list[ast.AST] = list(st_.decorator_list)
                if is_class:
                    head_nodes += list(st_.bases) + [k.value for k in st_.keywords]
                else:
                    a = st_.args
                    head_nodes += [d for d in a.defaults + a.kw_defaults if d is not None]
                    head_nodes += [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs if x.annotation]
                    if st_.returns:
                        head_nodes.append(st_.returns)
                # Lines that run when the definition executes and could raise there: bases, defaults that are not
                # literals, and decorators. A literal default or an annotation cannot fail by changing its value.
                import_lines: set[int] = set()
                risky: list[ast.AST] = list(st_.decorator_list)
                if is_class:
                    risky += list(st_.bases) + [k.value for k in st_.keywords]
                else:
                    for dflt in st_.args.defaults + st_.args.kw_defaults:
                        if dflt is None:
                            continue
                        try:
                            ast.literal_eval(dflt)
                        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                            risky.append(dflt)
                for h in risky:
                    import_lines |= _line_span(h)
                if import_time:
                    info.import_refs |= _callees_of(head_nodes) | {_decorator_name(d) for d in st_.decorator_list}
                start = min([st_.lineno] + [d.lineno for d in st_.decorator_list])
                body_start = st_.body[0].lineno if st_.body else st_.lineno
                info.defs.append(DefRec(
                    name=st_.name, start=start, end=st_.end_lineno or st_.lineno, body_start=body_start,
                    import_lines=import_lines, owner=owner,
                    registered=any(_decorator_name(d) not in _PLAIN_DECORATORS for d in st_.decorator_list),
                    is_class=is_class, decorated=bool(st_.decorator_list),
                    # a class stands only for its own statements: each method has its own record, and a class
                    # that inherited every method's mentions would make one change reach every user of the class
                    refs=(_refs_of([s2 for s2 in st_.body if not isinstance(s2, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))] + head_nodes, skip)
                          if is_class else _refs_of(st_.body, skip)),
                ))
                if is_class:
                    plain = [s2 for s2 in st_.body if not isinstance(s2, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                    if import_time:
                        info.import_refs |= _callees_of(plain)
                visit(st_.body, st_.name if is_class else owner, import_time and is_class)
            else:
                if import_time and isinstance(st_, (ast.Assign, ast.AnnAssign)) and getattr(st_, "value", None) is not None:
                    targets = st_.targets if isinstance(st_, ast.Assign) else [st_.target]
                    tnames = {t.id for t in targets if isinstance(t, ast.Name)}
                    try:
                        ast.literal_eval(st_.value)
                        literal = bool(tnames) and len(tnames) == len(targets)
                    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                        literal = False
                    if literal:
                        info.literal_assigns.append((st_.lineno, st_.end_lineno or st_.lineno, tnames))
                binds_only = isinstance(st_, (ast.Import, ast.ImportFrom)) or (
                    isinstance(st_, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in st_.targets))
                if import_time and not binds_only:  # only a call can run a changed function body at import
                    info.import_refs |= _callees_of([st_])
                    info.value_refs |= _value_names([st_])
                for field_name in ("body", "orelse", "finalbody"):
                    sub = getattr(st_, field_name, None)
                    if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                        visit(sub, owner, import_time)
                for h in getattr(st_, "handlers", []) or []:
                    visit(h.body, owner, import_time)

    visit(tree.body, None, True)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node.func) in ("getattr", "hasattr", "setattr", "delattr") \
                and len(node.args) >= 2:
            pre = _literal_prefix(node.args[1])
            if pre:
                info.getattr_prefixes.add(pre)
    # Registration looks a function up by a string later (codecs.register, plugins, signal handlers): a name
    # that no test mentions can still be the one that runs, so changes in such a file stay file-level.
    info.registers = any(n.lower().startswith("register") or n.lower().endswith("register") or n == "setattr"
                         for n in info.import_refs)
    if test_side:
        _analyse_tests(tree, info, skip)
    return info


_SAFE_BASES = {"object", "TestCase", "IsolatedAsyncioTestCase", "ABC", "Generic"}
_MODULE_HOOKS = {"setUpModule", "tearDownModule", "setup_module", "teardown_module", "setup_function",
                 "teardown_function", "setup", "teardown"}


def _arg_names(fn) -> set[str]:
    a = fn.args
    out = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    for extra in (a.vararg, a.kwarg):
        if extra is not None:
            out.add(extra.arg)
    return out


def _is_autouse(fn) -> bool:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and _callee(d.func) == "fixture":
            if any(k.arg == "autouse" and isinstance(k.value, ast.Constant) and k.value.value is True for k in d.keywords):
                return True
    return False


def _test_own_refs(fn, skip: set[int]) -> set[str]:
    head = list(fn.decorator_list) + [d for d in fn.args.defaults + fn.args.kw_defaults if d is not None]
    return _refs_of(list(fn.body) + head, skip) | _arg_names(fn) | {fn.name}


def _analyse_tests(tree: ast.Module, info: NameInfo, skip: set[int]) -> None:
    """Per-test mentions for a test-side file. Anything shared by the tests of a class or module is added to
    every one of them, so a test is never judged by less than what runs with it."""
    shared_none: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                info.test_funcs[(None, node.name)] = _test_own_refs(node, skip)
            elif _is_autouse(node) or node.name.startswith("pytest_") or node.name in _MODULE_HOOKS:
                info.autouse.add(node.name)
        elif isinstance(node, ast.ClassDef):
            shared: set[str] = set()
            for base in node.bases:
                b = _callee(base)
                if b not in _SAFE_BASES:
                    info.foreign_bases.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("test"):
                        info.test_funcs[(node.name, sub.name)] = _test_own_refs(sub, skip)
                    else:
                        shared |= _refs_of([sub], skip) | {sub.name}
                elif not isinstance(sub, (ast.Import, ast.ImportFrom)):
                    shared |= _refs_of([sub], skip)
            shared |= _refs_of(list(node.decorator_list), skip)
            info.shared[node.name] = shared
        else:
            shared_none |= _refs_of([node], skip)
    info.shared[None] = shared_none


@dataclass
class FileDiff:
    added: dict[int, str] = field(default_factory=dict)  # new line number -> text
    removed: list[tuple[int, str]] = field(default_factory=list)  # (position in the new file, text)
    context: set[str] = field(default_factory=set)  # function/class names from hunk headers


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def diff_hunks(diff: str) -> dict[str, FileDiff]:
    out: dict[str, FileDiff] = {}
    cur: FileDiff | None = None
    new_line = 0
    in_hunk = False
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            m = _HDR.match(ln)
            cur = out.setdefault(m.group(2), FileDiff()) if m else None
            in_hunk = False
        elif ln.startswith("+++ ") and not in_hunk:
            path = re.sub(r"^b/", "", ln[4:].split("\t")[0])
            if path != "/dev/null":
                cur = out.setdefault(path, FileDiff())
        elif ln.startswith("@@"):
            m = _HUNK.match(ln)
            if m and cur is not None:
                new_line = int(m.group(3))
                in_hunk = True
                cur.context.update(re.findall(r"(?:def|class)\s+([A-Za-z_]\w*)", m.group(5)))
        elif in_hunk and cur is not None:
            if ln.startswith("+") and not ln.startswith("+++"):
                cur.added[new_line] = ln[1:]
                new_line += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                cur.removed.append((new_line, ln[1:]))
            elif ln.startswith(" "):
                new_line += 1
    return out


def is_test_side(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")
            or any(p in ("test", "tests", "testing") for p in parts[:-1]))


_CODE_LINE = re.compile(
    r"^\s*(return|raise|if|elif|else|for|while|def|class|import|from|with|try|except|finally|assert|yield|del|pass|"
    r"break|continue|lambda|await|async|global|nonlocal)\b|^\s*[\w.\[\]]+\s*[-+*/|&]?=[^=]|^\s*[\w.]+\("
)


def _blank_or_comment(text: str) -> bool:
    t = text.strip()
    return not t or t.startswith("#")


def name_change(info: PyInfo, fd: FileDiff | None, source_lines: list[str],
                why: list | None = None, bodies: list | None = None) -> tuple[set[str], bool]:
    """(names affected by the change, whether the change must stay file-level).

    A change is file-level (coarse) when it touches import-time code, a class body outside a method, a
    decorator or default value, a registered function, a module-level dunder, or when nothing is known
    about the changed lines."""
    ni = info.names
    if ni is None or fd is None or not (fd.added or fd.removed) or ni.registers:
        if why is not None:
            why.append("registers" if ni and ni.registers else "no-line-info")
        return set(), True
    positions: set[int] = set()
    # Docstrings are code: some projects read them at run time (command-line help, registries), so a docstring
    # edit counts as a change to its function, and a module docstring as a change to the whole file.
    skip_doc: set[int] = set()
    for line, text in fd.added.items():
        if not _blank_or_comment(text) and line not in skip_doc:
            positions.add(line)
    only_doc_added = bool(fd.added) and all(line in skip_doc for line in fd.added)
    for pos, text in fd.removed:
        if _blank_or_comment(text):
            continue
        if only_doc_added and not _CODE_LINE.match(text):
            continue  # a docstring was rewritten: its old lines are prose
        if pos in fd.added:
            continue  # a replacement: the added lines at this position stand for the change
        # A deleted line affects the code around it. If its neighbours are blank or comment lines, look further out
        # for the nearest code on each side, so that a deletion between comments is not mistaken for no change.
        before = pos - 1
        while before >= 1 and (_blank_or_comment(source_lines[before - 1]) or before in skip_doc):
            before -= 1
        after = pos
        while after <= len(source_lines) and (_blank_or_comment(source_lines[after - 1]) or after in skip_doc):
            after += 1
        for q in (before, after):
            if 1 <= q <= len(source_lines):
                positions.add(q)
    names: set[str] = set()
    for _, text in fd.removed:
        m = re.match(r"\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", text)
        if m:
            if not any(d.name == m.group(1) for d in ni.defs):
                if why is not None:
                    why.append("def-removed")
                return set(), True  # a definition was removed or renamed: importers can break
            names.add(m.group(1))
    coarse = False
    for L in sorted(positions):
        lit = next((a for a in ni.literal_assigns if a[0] <= L <= a[1]), None)
        enclosing = [d for d in ni.defs if d.start <= L <= d.end]
        if lit is not None and not any(d.body_start <= L <= d.end and not d.is_class for d in enclosing):
            names |= lit[2]  # a literal value changed: only the name it is bound to is affected
            if bodies is not None:
                bodies.append(None)  # not a function body: the dynamic core cannot speak for it
            continue
        if not enclosing:
            if why is not None:
                why.append("module-level")
            return set(), True  # module-level code runs at import
        enclosing.sort(key=lambda d: d.end - d.start)
        inner = enclosing[0]
        if L in inner.import_lines or any(L in d.import_lines for d in enclosing):
            if why is not None:
                why.append("header-default-decorator")
            return set(), True
        if inner.is_class and L >= inner.body_start and not any(
                d is not inner and not d.is_class and d.start <= L <= d.end for d in enclosing):
            if why is not None:
                why.append("class-body")
            return set(), True  # a class-body statement (attribute, nested config) outside every method
        if bodies is not None:
            fn = next((d for d in enclosing if not d.is_class and d.body_start <= L <= d.end), None)
            # by qualified name (lines shift between commits); a decorated function is left to the static rules,
            # because a cache or a wrapper means the tests that depend on it need not execute its body
            bodies.append((fn.owner, fn.name) if fn and not fn.decorated else None)
        for d in enclosing:
            if d.registered:
                if why is not None:
                    why.append("decorated")
                return set(), True
            if d.name.startswith("__") and d.name.endswith("__"):
                if d.owner is None:
                    return set(), True
                names.add(d.owner)  # an implicit call: the class stands for it
            elif not d.is_class:
                names.add(d.name)  # a method change reaches its callers by the method's name, not by its class
            elif d is inner:
                names.add(d.name)
    if not positions and not names:
        return set(), False  # comments, blank lines and docstrings only
    names |= {n for n in fd.context if any(d.name == n for d in ni.defs)}  # the function a hunk header names
    return names, coarse


def affected_names(graph: "RepoGraph", seeds: set[str]) -> set[str]:
    """Close a set of names under 'a definition that mentions an affected name is affected'."""
    callers = graph.callers()
    affected = set(seeds)
    queue = list(seeds)
    while queue:
        n = queue.pop()
        for d in callers.get(n, ()):
            if d not in affected:
                affected.add(d)
                queue.append(d)
    return affected


# --------------------------------------------------------------------------- #
# The core
# --------------------------------------------------------------------------- #
def _full(tests: list[DiscoveredTest], reason: str, changed: list[str] | None = None) -> Selection:
    ids = [t.node_id for t in tests]
    return Selection(mode="full", reason=reason, total=len(tests), core=ids, selected=ids, changed=changed or [])


def select_tests(root: Path, diff: str, tests: list[DiscoveredTest], graph: RepoGraph | None = None,
                 level: str = "name", taint_dynamic: bool = True, granularity: str = "function",
                 coverage: "CoverageMap | None" = None) -> Selection:
    """The deterministic core. No model is consulted; failures of any kind select every test. Whatever the core
    selects, a change that removes or weakens tests is recorded in `weakened`."""
    sel = _select(root, diff, tests, graph, level, taint_dynamic, granularity, coverage)
    try:
        changed = parse_diff(diff) or []
        sel.weakened = weakened_tests(changed, diff_hunks(diff)) if changed else []
    except Exception as exc:  # noqa: BLE001 - if the check cannot run, say so: the change goes to a person
        sel.weakened = [f"could not check for removed tests ({type(exc).__name__})"]
    return sel


def _select(root, diff, tests, graph, level, taint_dynamic, granularity, coverage) -> Selection:
    try:
        changed = parse_diff(diff)
        if changed is None:
            return _full(tests, "the diff is empty or could not be read")
        names = [c.path for c in changed]
        for c in changed:
            for p in (c.path, c.old_path):
                if p and kind_of(p) == "config":
                    return _full(tests, f"a build or test configuration file changed: {p}", names)
        g = graph or RepoGraph(root)
        return _core(g, changed, tests, diff_hunks(diff), level, taint_dynamic, granularity, coverage)
    except Exception as exc:  # noqa: BLE001 - fail closed: an analysis error must never shrink the run
        return _full(tests, f"analysis failed ({type(exc).__name__}: {str(exc)[:120]})")


class CoverageMap:
    """Which tests executed which function bodies, recorded by one run of the suite on the unmodified project.

    Built from coverage.py contexts (pytest-cov `--cov-context=test`): for every executed line, every test that
    executed it. A line belongs to the innermost function whose body contains it. The map is only trusted for
    changes inside function bodies; everything else keeps the static rules."""

    def __init__(self, by_body: dict[tuple, set[str]], files: set[str], outside: set | None = None) -> None:
        self.by_body = by_body
        self.files = files
        # bodies that also ran outside any test (while modules were imported or tests collected): which tests
        # depend on that run is unknown, so a change to them is left to the static rules
        self.outside = outside or set()

    @classmethod
    def from_json(cls, data: dict) -> "CoverageMap":
        """A stored recording, as written by `shadow-record` (tests listed once, bodies by index)."""
        tests = data["tests"]
        by_body = {(f, o, n): {tests[i] for i in ids} for f, o, n, ids in data["bodies"]}
        return cls(by_body, set(data["files"]), {tuple(k) for k in data["outside"]})

    @classmethod
    def from_sqlite(cls, db: Path, graph: "RepoGraph", root: Path) -> "CoverageMap":
        """Read coverage.py's data file directly (tables `file`, `context`, `line_bits`; lines as numbits)."""
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            paths = dict(con.execute("select id, path from file"))
            ctxs = dict(con.execute("select id, context from context"))
            by_file: dict[str, dict[str, list[str]]] = {}
            for file_id, ctx_id, bits in con.execute("select file_id, context_id, numbits from line_bits"):
                test = (ctxs.get(ctx_id) or "").rsplit("|", 1)[0]  # "" is code that ran outside any test
                lines = by_file.setdefault(paths[file_id], {})
                for i, byte in enumerate(bits):
                    for b in range(8):
                        if byte & (1 << b):
                            lines.setdefault(str(i * 8 + b), []).append(test)
        finally:
            con.close()
        return cls.from_coverage_json({"files": {f: {"contexts": c} for f, c in by_file.items()}}, graph, root)

    @classmethod
    def from_coverage_json(cls, data: dict, graph: "RepoGraph", root: Path) -> "CoverageMap":
        by_body: dict[tuple, set[str]] = {}
        files: set[str] = set()
        outside: set = set()
        real_root = Path(root).resolve()
        for fname, fd in (data.get("files") or {}).items():
            path = Path(fname)
            if path.is_absolute():
                try:
                    path = path.resolve().relative_to(real_root)  # /var and /private/var are the same directory
                except ValueError:
                    continue  # outside the project (an installed package)
            rel = path.as_posix()
            info = graph.files.get(rel)
            if info is None or info.names is None or "contexts" not in fd:
                continue  # without per-test contexts the file has no map: the static rules apply to it
            files.add(rel)
            funcs = [d for d in info.names.defs if not d.is_class]
            for line, ctxs in (fd.get("contexts") or {}).items():
                L = int(line)
                inner = None
                for d in funcs:
                    if d.body_start <= L <= d.end and (inner is None or d.end - d.start < inner.end - inner.start):
                        inner = d
                if inner is None:
                    continue
                key = (rel, inner.owner, inner.name)
                tests = {c.rsplit("|", 1)[0] for c in ctxs}
                if "" in tests:
                    outside.add(key)
                tests.discard("")
                if tests:
                    by_body.setdefault(key, set()).update(tests)
        return cls(by_body, files, outside)


def _key(t: DiscoveredTest) -> tuple:
    """(class or None, function) of a pytest node id, without parametrization."""
    parts = t.node_id.split("::")[1:]
    parts = [re.sub(r"\[.*$", "", x) for x in parts]
    if len(parts) >= 2:
        return (parts[-2], parts[-1])
    return (None, parts[-1] if parts else t.name)


def _core(g: RepoGraph, changed: list[Changed], tests: list[DiscoveredTest], fds: dict[str, FileDiff],
          level: str, taint_dynamic: bool = True, granularity: str = "function",
          coverage: "CoverageMap | None" = None) -> Selection:
    nodes_by_file: dict[str, list[DiscoveredTest]] = {}
    for t in tests:
        nodes_by_file.setdefault(t.file, []).append(t)
    test_files = sorted(nodes_by_file)
    keys = {t.node_id: _key(t) for t in tests}
    fn_level = level == "name" and granularity == "function"
    selected: set[str] = set()
    comment_only: set[str] = set()
    inert: list[str] = []
    uncovered: list[str] = []
    oos: list[str] = []

    def file_nodes(files) -> set[str]:
        return {t.node_id for f in files for t in nodes_by_file.get(f, ())}

    def tests_mentioning(tf: str, affected: set[str]) -> set[str]:
        """The tests of a file that mention an affected name (one decision per test, or per file)."""
        if fn_level:
            out = set()
            for t in nodes_by_file[tf]:
                refs, star = g.test_fn_refs(tf, keys[t.node_id])
                if star or (refs & affected):
                    out.add(t.node_id)
            return out
        refs, star = g.test_refs(tf)
        return file_nodes([tf]) if (star or (refs & affected)) else set()

    def read_lines(p: str) -> list[str] | None:
        try:
            return (g.root / p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    def name_hits(c: Changed, p: str) -> set[str] | None:
        """Tests selected by name-level analysis of a modified library file, or None to stay file-level."""
        if level != "name" or c.status != "modified" or p != c.path:
            return None
        info = g.files.get(p)
        lines = read_lines(p)
        if info is None or info.names is None or lines is None:
            return None
        bodies: list = []
        names, coarse = name_change(info, fds.get(p), lines, bodies=bodies)
        if coarse:
            return None
        if not names:
            comment_only.add(p)
            return set()
        if (coverage is not None and p in coverage.files and bodies and all(b is not None for b in bodies)
                and not any((p, *b) in coverage.outside for b in bodies)):
            # Every changed line lies in a function body that the recorded run can speak for: the tests that
            # executed those bodies are the ones whose outcome can change. A body no test executed is unreached.
            hit_ids: set[str] = set()
            for owner, name in set(bodies):
                hit_ids |= coverage.by_body.get((p, owner, name), set())
            known = {t.node_id for t in tests}
            return {x for x in hit_ids if x in known}
        affected = affected_names(g, names)
        if g.dispatched_by_name(affected):
            return None  # a lookup by string can reach it: stay at file level
        import_aff = {f for f, i in g.files.items()
                      if i.names is not None and not is_test_side(f) and (i.names.import_refs & affected)}
        out: set[str] = set()
        for tf in test_files:
            cl = g.closure(tf)
            if cl & import_aff:
                out |= file_nodes([tf])
            elif p in cl:
                out |= tests_mentioning(tf, affected)
        return out

    def test_file_hits(c: Changed, p: str) -> set[str]:
        """A modified test file: the tests whose own code changed, or all of them when shared code did."""
        everything = file_nodes([p])
        if not fn_level or c.status != "modified" or p != c.path:
            return everything
        info = g.files.get(p)
        lines = read_lines(p)
        if info is None or info.names is None or lines is None:
            return everything
        names, coarse = name_change(info, fds.get(p), lines)
        if coarse:
            return everything
        if not names:
            comment_only.add(p)
            return set()
        affected = affected_names(g, names)
        return {t.node_id for t in nodes_by_file[p]
                if t.name in names or (g.test_fn_refs(p, keys[t.node_id])[0] & affected)}

    def impacted(c: Changed) -> set[str]:
        hit: set[str] = set()
        for p in [x for x in (c.path, c.old_path) if x]:
            kind = kind_of(p)
            base = p.rsplit("/", 1)[-1].lower()
            if kind == "python":
                if p in nodes_by_file:
                    hit |= test_file_hits(c, p)
                else:  # a test that loads modules dynamically can reach any source file
                    if taint_dynamic:
                        hit |= file_nodes(tf for tf in test_files if g.tainted(tf))
                    refined = name_hits(c, p)
                    if refined is not None:
                        hit |= refined
                    else:
                        hit |= file_nodes(tf for tf in test_files if p in g.closure(tf))
                    # A recorded run saw every in-process import, computed or not; only started processes
                    # escape it. Without a recording, both routes are bounded by string literals.
                    recorded = coverage is not None and p in coverage.files
                    hit |= file_nodes(tf for tf in test_files if p in g.extra_reach(tf, spawn_only=recorded))
                hit |= file_nodes(tf for tf in test_files  # run through a subprocess or opened by name
                                  if g.files.get(tf) and base in g.files[tf].text and tf != p)
                if c.status in ("deleted", "renamed"):
                    gone = g.keys_of_path(p)
                    importers = {f for f, u in g.unresolved.items() if u & gone}
                    hit |= file_nodes(tf for tf in test_files if g.closure(tf) & importers)
            elif kind == "conftest":
                d = p.rsplit("/", 1)[0] if "/" in p else ""
                hit |= file_nodes(tf for tf in test_files if not d or tf.startswith(d + "/"))
            elif kind == "other":
                d = p.rsplit("/", 1)[0] if "/" in p else ""
                for tf in test_files:
                    td = tf.rsplit("/", 1)[0] if "/" in tf else ""
                    if (td and (d == td or d.startswith(td + "/"))) or any(
                            base in g.files[f].text for f in g.closure(tf) if f in g.files):
                        hit |= file_nodes([tf])
        return hit

    for c in changed:
        kinds = {kind_of(p) for p in (c.path, c.old_path) if p}
        if c.path in nodes_by_file and kinds == {"inert"}:
            selected |= file_nodes([c.path])  # a text file the runner collects (doctests) is a test, not prose
            continue
        if kinds == {"inert"}:
            inert.append(c.path)
            continue
        hit = impacted(c)
        if hit:
            selected |= hit
        elif c.path in comment_only:
            inert.append(c.path)  # only comments or blank lines changed
        elif out_of_scope(c.path):
            oos.append(c.path)
        else:
            uncovered.append(c.path)
    core = [t.node_id for t in tests if t.node_id in selected]
    return Selection(
        mode="change", reason=f"{level}/{granularity}", total=len(tests), core=core, selected=list(core),
        changed=[c.path for c in changed], inert=inert, uncovered=uncovered, out_of_scope=oos,
    )


def with_advice(sel: Selection, advised: list[str], known: set[str]) -> Selection:
    """Add advised tests to a selection. Advice can add; it can never remove or replace a core test."""
    if sel.mode == "full":
        return sel
    have = set(sel.selected)
    extra = [n for n in dict.fromkeys(advised) if n in known and n not in have]
    sel.added = extra
    sel.selected = sel.selected + extra
    return sel
