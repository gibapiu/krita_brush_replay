import ast
from pathlib import Path

PACKAGE = Path("krita_brush_replay")
PURE_DIRECTORIES = (PACKAGE / "domain", PACKAGE / "app")
HOST_PREFIXES = (
    "krita_brush_replay.infrastructure",
    "krita_brush_replay.ui",
    "krita_brush_replay.plugin",
    "krita_brush_replay.runtime",
    "krita_brush_replay.observability",
    "krita_brush_replay.composition",
    "krita",
    "PyQt5",
    "PyQt6",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_pure_layers_do_not_import_qt_krita_or_host_layers() -> None:
    violations: list[str] = []
    for directory in PURE_DIRECTORIES:
        for path in directory.rglob("*.py"):
            for module in imported_modules(path):
                if any(
                    module == prefix or module.startswith(f"{prefix}.") for prefix in HOST_PREFIXES
                ):
                    violations.append(f"{path}: {module}")

    assert violations == []


def test_direct_qt_binding_imports_are_confined_to_facade() -> None:
    facade = PACKAGE / "infrastructure" / "qt_facade.py"
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if path == facade:
            continue
        for module in imported_modules(path):
            if module.startswith(("PyQt5", "PyQt6")):
                violations.append(f"{path}: {module}")

    assert violations == []


def test_krita_imports_are_confined_to_infrastructure() -> None:
    allowed = {
        PACKAGE / "infrastructure" / "krita_facade.py",
        PACKAGE / "infrastructure" / "krita_host.py",
        PACKAGE / "infrastructure" / "krita_replay.py",
        PACKAGE / "infrastructure" / "krita_brush_state.py",
        PACKAGE / "infrastructure" / "krita_pressure.py",
    }
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if path in allowed:
            continue
        for module in imported_modules(path):
            if module == "krita" or module.startswith("krita."):
                violations.append(f"{path}: {module}")

    assert violations == []


def test_core_does_not_import_observability_or_composition() -> None:
    paths = [
        PACKAGE / "runtime.py",
        *(PACKAGE / "ui").glob("*.py"),
        *(PACKAGE / "infrastructure").glob("*.py"),
        *(PACKAGE / "domain").glob("*.py"),
        *(PACKAGE / "app").glob("*.py"),
    ]
    forbidden = ("krita_brush_replay.observability", "krita_brush_replay.composition")
    assert [
        (path, module)
        for path in paths
        for module in imported_modules(path)
        if module.startswith(forbidden)
    ] == []
