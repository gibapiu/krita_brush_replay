"""Explicit suite selection: no pretend Qt modules or silent environment skips."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

pytest_plugins = ("tests.support.fixtures",)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--qt-api", choices=("none", "pyqt5", "pyqt6"), default="none")
    parser.addoption("--performance", action="store_true", help="Run memory/latency budgets")
    parser.addoption("--test-order-seed", type=int, help="Shuffle collected tests reproducibly")


def pytest_configure(config: pytest.Config) -> None:
    binding = config.getoption("--qt-api")
    if binding != "none":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        package = {"pyqt5": "PyQt5", "pyqt6": "PyQt6"}[binding]
        try:
            importlib.import_module(f"{package}.QtWidgets")
        except ImportError as error:
            raise pytest.UsageError(
                f"--qt-api={binding} requires real {package}: {error}"
            ) from error
    for argument in config.invocation_params.args:
        path = Path(argument.split("::", 1)[0])
        if "performance" in path.parts and not config.getoption("--performance"):
            raise pytest.UsageError("Selecting tests/performance requires --performance")


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path.name == "performance" and not config.getoption("--performance"):
        return True
    return None


def pytest_report_header(config: pytest.Config) -> str:
    return (
        f"Qt: {config.getoption('--qt-api')}; "
        f"performance: {config.getoption('--performance')}; Krita host: not exercised"
    )


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    binding = config.getoption("--qt-api")
    budgets = "selected" if config.getoption("--performance") else "not selected"
    terminalreporter.write_line(
        f"Qt integration: {binding}; performance: {budgets}; real Krita: not exercised"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "performance" in item.path.parts:
            item.add_marker(pytest.mark.performance)
        if "algorithms" in item.path.parts:
            item.add_marker(pytest.mark.algorithms)
    if config.getoption("--qt-api") == "none":
        qt_tests = [item for item in items if item.get_closest_marker("qt") is not None]
        if qt_tests and len(qt_tests) == len(items):
            raise pytest.UsageError("Selected tests require --qt-api=pyqt5 or pyqt6")
        config.hook.pytest_deselected(items=qt_tests)
        items[:] = [item for item in items if item not in qt_tests]
    seed = config.getoption("--test-order-seed")
    if seed is not None:
        import random

        random.Random(seed).shuffle(items)
