"""Qt binding already loaded by Krita."""

import sys

if "PyQt6" in sys.modules:
    from PyQt6 import QtCore, QtGui, QtWidgets
elif "PyQt5" in sys.modules:
    from PyQt5 import QtCore, QtGui, QtWidgets
else:
    raise ImportError("Krita has not loaded PyQt5 or PyQt6")


__all__ = ["QtCore", "QtGui", "QtWidgets"]
