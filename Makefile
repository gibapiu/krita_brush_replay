.PHONY: test test-qt6 test-qt5 performance quality

PYTHON ?= python3

test:
	$(PYTHON) -m pytest -q

test-qt6:
	$(PYTHON) -m pytest -q --qt-api=pyqt6

test-qt5:
	$(PYTHON) -m pytest -q --qt-api=pyqt5

performance:
	$(PYTHON) -m pytest -q --performance tests/performance

quality:
	$(PYTHON) -m ruff check .
	$(PYTHON) tools/verify_dependencies.py
