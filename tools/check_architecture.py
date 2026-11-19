"""Static dependency policy, independent of the invoking working directory."""

from __future__ import annotations

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "krita_brush_replay"
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
KRITA_MODULES = {
    "infrastructure/krita_facade.py",
    "infrastructure/krita_host.py",
    "infrastructure/krita_replay.py",
    "infrastructure/krita_brush_state.py",
    "infrastructure/krita_pressure.py",
}


def imported_modules(path: Path) -> set[str]:
    relative = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = relative.parts[:-1]
    package = ".".join(parts)
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def main() -> int:
    files = sorted(PACKAGE.rglob("*.py"))
    if not files:
        print(f"No production Python files found at {PACKAGE}", file=sys.stderr)
        return 1
    violations = []
    for path in files:
        relative = path.relative_to(PACKAGE)
        for module in sorted(imported_modules(path)):
            reason = None
            if relative.parts[0] in {"domain", "app"} and matches(module, HOST_PREFIXES):
                reason = "pure layers must not depend on host/composition layers"
            elif matches(module, ("PyQt5", "PyQt6")) and relative.as_posix() != (
                "infrastructure/qt_facade.py"
            ):
                reason = "Qt binding imports belong in qt_facade"
            elif matches(module, ("krita",)) and relative.as_posix() not in KRITA_MODULES:
                reason = "Krita imports belong in the Krita adapters"
            elif relative.parts[0] in {"domain", "app", "infrastructure", "ui", "runtime.py"}:
                if matches(
                    module, ("krita_brush_replay.observability", "krita_brush_replay.composition")
                ):
                    reason = "core must not depend on instrumentation/composition"
            if reason:
                violations.append(f"{relative}: {module}: {reason}")
    for violation in violations:
        print(violation, file=sys.stderr)
    if not violations:
        print(f"Architecture policy passed ({len(files)} production files)")
    return bool(violations)


if __name__ == "__main__":
    raise SystemExit(main())
