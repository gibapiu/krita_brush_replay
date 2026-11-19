"""Check the plugin's imports."""

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
        print(f"No Python files found in {PACKAGE}", file=sys.stderr)
        return 1
    problems = []
    for path in files:
        relative = path.relative_to(PACKAGE)
        for module in sorted(imported_modules(path)):
            reason = None
            if relative.parts[0] in {"domain", "app"} and matches(module, HOST_PREFIXES):
                reason = "domain and app must not import this module"
            elif matches(module, ("PyQt5", "PyQt6")) and relative.as_posix() != (
                "infrastructure/qt_facade.py"
            ):
                reason = "import Qt through infrastructure/qt_facade.py"
            elif matches(module, ("krita",)) and relative.as_posix() not in KRITA_MODULES:
                reason = "import Krita only in the Krita adapter files"
            elif relative.parts[0] in {"domain", "app", "infrastructure", "ui", "runtime.py"}:
                if matches(
                    module, ("krita_brush_replay.observability", "krita_brush_replay.composition")
                ):
                    reason = "do not import diagnostics or plugin setup here"
            if reason:
                problems.append(f"{relative} imports {module}: {reason}")
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"Dependencies OK ({len(files)} files)")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
