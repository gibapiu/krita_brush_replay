
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "krita_brush_replay"
HOST_LAYERS = tuple(
    "krita_brush_replay." + layer
    for layer in ("infrastructure", "ui", "plugin", "runtime", "observability", "composition")
)
HOST_APIS = ("krita", "PyQt5", "PyQt6")


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_core_dependency_boundaries():
    violations = []
    for path in PACKAGE.rglob("*.py"):
        layer = path.relative_to(PACKAGE).parts[0]
        forbidden = ()
        if layer == "domain":
            forbidden += ("krita_brush_replay.app",)
        if layer in ("domain", "app"):
            forbidden += HOST_LAYERS + HOST_APIS
        if layer in ("domain", "app", "infrastructure", "ui", "runtime.py"):
            forbidden += (
                "krita_brush_replay.observability", "krita_brush_replay.composition", "logging"
            )
        if layer != "infrastructure":
            forbidden += HOST_APIS
        if path.is_relative_to(PACKAGE / "infrastructure" / "host"):
            forbidden += ("krita_brush_replay.infrastructure.storage",)
        if path.is_relative_to(PACKAGE / "infrastructure" / "storage"):
            forbidden += ("krita_brush_replay.infrastructure.host",) + HOST_APIS
        if path.name != "qt_facade.py":
            forbidden += ("PyQt5", "PyQt6")
        for module in imports(path):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(PACKAGE)} imports {module}")
    assert violations == []
