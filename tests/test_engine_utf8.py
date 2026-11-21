"""Keep engine text I/O independent of the platform's default encoding."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def is_binary_mode(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "b" in node.value
    if isinstance(node, ast.IfExp):
        return is_binary_mode(node.body) and is_binary_mode(node.orelse)
    return False


def missing_encodings(source):
    errors = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in {"read_text", "write_text"}:
            text = True
        elif name in {"open", "TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile"}:
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in {"Image", "av", "wave"}:
                    continue
            default = "w+b" if name != "open" else "r"
            position = (
                0
                if name != "open"
                else (
                    0
                    if isinstance(func, ast.Attribute)
                    and not (isinstance(func.value, ast.Name) and func.value.id == "io")
                    else 1
                )
            )
            mode = next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
            if mode is None and len(node.args) > position:
                mode = node.args[position]
            text = "b" not in default if mode is None else not is_binary_mode(mode)
        else:
            continue
        if text and not any(kw.arg == "encoding" for kw in node.keywords):
            errors.append(node.lineno)
    return errors


@pytest.mark.parametrize(
    ("source", "missing"),
    [
        ('open("x")', True),
        ('open("x", "rb")', False),
        ('open("x", "ab" if append else "wb")', False),
        ('open("x", mode)', True),
        ('open("x", "rb" if binary else "w")', True),
        ('io.open("x", mode="w")', True),
        ('path.open("w", encoding="utf-8")', False),
        ("path.read_text()", True),
        ('path.write_text("x", encoding="utf-8")', False),
        ('tempfile.TemporaryFile(mode="w+t")', True),
        ("tempfile.TemporaryFile()", False),
        ('Image.open("x")', False),
    ],
)
def test_encoding_guard_examples(source, missing):
    assert bool(missing_encodings(source)) is missing


def test_engine_text_files_have_explicit_encoding():
    errors = []
    for path in sorted((ROOT / "src/localsr").rglob("*.py")):
        if "vendor" in path.parts:
            continue
        errors.extend(
            f"{path.relative_to(ROOT)}:{line}"
            for line in missing_encodings(path.read_text(encoding="utf-8"))
        )
    assert not errors, "\n".join(errors)


def test_worker_spec_enables_utf8_mode():
    tree = ast.parse((ROOT / "packaging/tauri_worker.spec").read_text(encoding="utf-8"))
    exe = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EXE"
    )
    assert any(
        isinstance(arg, (ast.List, ast.Tuple))
        and any(
            isinstance(item, ast.Tuple)
            and ast.literal_eval(item) in (("X utf8", None, "OPTION"), ("X utf8=1", None, "OPTION"))
            for item in arg.elts
        )
        for arg in exe.args
    )


def test_source_worker_launches_enable_utf8_mode():
    source = (ROOT / "desktop/src-tauri/src/worker.rs").read_text(encoding="utf-8")
    for name in ("start_worker", "codec_helper_command"):
        body = source.split(f"fn {name}(", 1)[1].split("\n}", 1)[0]
        assert 'command.env("PYTHONUTF8", "1")' in body
