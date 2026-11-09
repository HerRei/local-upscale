"""Keep test and script imports safe during collection on every platform."""

import ast
from pathlib import Path

import pytest

RESTRICTED = {
    "resource",
    "fcntl",
    "termios",
    "pwd",
    "grp",
    "tty",
    "pty",
    "objc",
    "Foundation",
    "AppKit",
}
ROOT = Path(__file__).resolve().parents[1]


def unsafe_imports(source):
    tree = ast.parse(source)
    errors = []

    def visit(statements, guarded=False, skipped=frozenset()):
        skipped = set(skipped)
        for node in statements:
            value = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "pytest"
                and value.func.attr == "importorskip"
                and value.args
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                skipped.add(value.args[0].value.split(".")[0])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name.split(".")[0] for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [(node.module or "").split(".")[0]]
                )
                if not guarded and (RESTRICTED.intersection(names) - skipped):
                    errors.append((node.lineno, names))
            elif isinstance(node, ast.If):
                condition = ast.unparse(node.test)
                platform_guard = any(
                    token in condition for token in ("sys.platform", "os.name", "platform.system(")
                )
                visit(node.body, guarded or platform_guard, skipped)
                visit(node.orelse, guarded or platform_guard, skipped)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                # importorskip is a safe alternative to importing the module.
                visit(node.body, guarded, skipped)
                for handler in node.handlers:
                    visit(handler.body, guarded, skipped)
                visit(node.orelse, guarded, skipped)
                visit(node.finalbody, guarded, skipped)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    visit(case.body, guarded, skipped)
            elif hasattr(node, "body") and isinstance(node.body, list):
                visit(node.body, guarded, skipped)
                if hasattr(node, "orelse"):
                    visit(node.orelse, guarded, skipped)

    visit(tree.body)
    return errors


@pytest.mark.parametrize(
    ("source", "unsafe"),
    [
        ("import resource", True),
        ("from Foundation import NSObject", True),
        ('if sys.platform == "darwin":\n import objc', False),
        ('objc = pytest.importorskip("objc")', False),
        ('pytest.importorskip("resource")\nimport resource', False),
        ("def f():\n import resource", False),
        ("class C:\n import resource", True),
        ("match x:\n case _:\n  import resource", True),
    ],
)
def test_import_guard_examples(source, unsafe):
    assert bool(unsafe_imports(source)) is unsafe


def test_scripts_and_tests_have_portable_module_imports():
    errors = []
    for directory in ("tests", "scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            for line, names in unsafe_imports(path.read_text(encoding="utf-8")):
                errors.append(f"{path.relative_to(ROOT)}:{line}: {names}")
    assert not errors, "\n".join(errors)


UNIX_ONLY_ATTRIBUTES = {
    "os": {
        "sysconf",
        "fork",
        "getuid",
        "geteuid",
        "getgid",
        "setsid",
        "killpg",
        "getpgid",
        "getpgrp",
        "setpgrp",
        "getloadavg",
        "statvfs",
        "mkfifo",
        "chown",
        "lchown",
        "nice",
    },
    "signal": {"SIGKILL", "SIGALRM", "SIGHUP", "SIGUSR1", "SIGUSR2", "alarm", "setitimer"},
}
# Uses that are safe although they are not inside a platform `if`, with the reason.
GUARDED_ELSEWHERE = {
    ("src/localsr/core/hardware.py", "sysconf"): "the function returns early on Windows",
}


def unguarded_unix_attributes(source):
    """Unix-only os/signal attributes used outside an `if` that names the platform.

    os.sysconf in the batch memory budget broke every batch test on Windows (2026-11-09)."""
    tree = ast.parse(source)
    found = []

    def visit(node, guarded):
        if isinstance(node, ast.If):
            condition = ast.unparse(node.test)
            platform = any(
                token in condition
                for token in (
                    "sys.platform",
                    "os.name",
                    "platform.system(",
                    "hasattr(os",
                    "hasattr(signal",
                )
            )
            visit_all([node.test], guarded)
            visit_all(node.body, guarded or platform)
            visit_all(node.orelse, guarded or platform)
            return
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in UNIX_ONLY_ATTRIBUTES.get(node.value.id, ())
            and not guarded
        ):
            found.append((node.lineno, node.attr))
        visit_all(list(ast.iter_child_nodes(node)), guarded)

    def visit_all(nodes, guarded):
        for child in nodes:
            visit(child, guarded)

    visit(tree, False)
    return found


def test_product_code_guards_unix_only_os_and_signal_calls():
    problems = []
    for path in sorted((ROOT / "src" / "localsr").rglob("*.py")):
        if "vendor" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line, attribute in unguarded_unix_attributes(path.read_text(encoding="utf-8")):
            if (relative, attribute) not in GUARDED_ELSEWHERE:
                problems.append(f"{relative}:{line} uses {attribute} without a platform guard")
    assert not problems, "\n".join(problems)


def test_the_unix_attribute_check_catches_an_unguarded_call():
    unguarded = (
        "import os\n\n"
        "def budget():\n"
        "    try:\n"
        "        return os.sysconf('SC_PHYS_PAGES')\n"
        "    except OSError:\n"
        "        return 0\n"
    )
    guarded = (
        "import os, sys\n\nif sys.platform != 'win32':\n    PAGES = os.sysconf('SC_PHYS_PAGES')\n"
    )
    assert unguarded_unix_attributes(unguarded) == [(5, "sysconf")]
    assert unguarded_unix_attributes(guarded) == []
