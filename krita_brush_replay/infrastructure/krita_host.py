"""Krita context and captured-input adapter."""

from __future__ import annotations

from collections.abc import Callable

from krita_brush_replay.domain.recording import (
    ContextRef,
    RecordingSession,
)
from krita_brush_replay.infrastructure.krita_replay import KritaReplayer
from krita_brush_replay.infrastructure.qt_facade import QtCore


class KritaHost:
    """Read Krita state, map captured input, and delegate stroke replay."""

    def __init__(self, krita_instance=None) -> None:
        self._krita = krita_instance if krita_instance is not None else _krita_instance()
        self._replayer = KritaReplayer(self.active_context)

    def active_context(self) -> ContextRef | None:
        document = self._active_document()
        if document is None:
            return None
        node = _call(document, "activeNode")
        root = _call(document, "rootNode")
        if node is None or root is None or _call(node, "paintAbility") != "PAINT":
            return None
        document_id = _stable_object_id(root)
        layer_id = _stable_object_id(node)
        if document_id is None or layer_id is None:
            return None
        return ContextRef(document_id, layer_id)

    def is_canvas_target(self, target) -> bool:
        window = _call(self._krita, "activeWindow")
        qwindow = _call(window, "qwindow")
        target_window = _call(target, "window")
        if qwindow is None or target_window is not qwindow:
            return False
        current = target
        while current is not None and current is not qwindow:
            class_name = _qt_class_name(current).lower()
            object_name = _string_call(current, "objectName").lower()
            if (
                "canvas" in class_name
                or "openglwidget" in class_name
                or object_name == "canvas"
            ):
                return True
            current = _call(current, "parentWidget")
        return False

    def capture_mapper(
        self,
    ) -> Callable[[tuple[float, float]], tuple[float, float]]:
        """Return one stable widget-to-image mapper for a captured stroke."""

        view = self._active_view()
        if view is None:
            raise RuntimeError("no active Krita view")
        canvas_transform = _call(view, "flakeToCanvasTransform")
        image_transform = _call(view, "flakeToImageTransform")
        if canvas_transform is None or image_transform is None:
            raise RuntimeError("Krita did not provide canvas transforms")
        widget_to_flake, invertible = canvas_transform.inverted()
        if not invertible:
            raise RuntimeError("the canvas transform is not invertible")
        transform = widget_to_flake * image_transform

        def to_image(position: tuple[float, float]) -> tuple[float, float]:
            image_position = transform.map(QtCore.QPointF(*position))
            return float(image_position.x()), float(image_position.y())

        return to_image

    def replay(
        self,
        session: RecordingSession,
        target: ContextRef,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        document = self._active_document()
        if document is None or self.active_context() != target:
            on_error(RuntimeError("the active document or layer changed"))
            return
        node = _call(document, "activeNode")
        self._replayer.replay(
            document,
            node,
            session,
            target,
            on_complete,
            on_error,
        )

    def _active_document(self):
        return _call(self._krita, "activeDocument")

    def _active_view(self):
        window = _call(self._krita, "activeWindow")
        return _call(window, "activeView")


def _krita_instance():
    from krita import Krita

    return Krita.instance()


def _call(source, name: str):
    if source is None:
        return None
    value = getattr(source, name, None)
    return value() if callable(value) else None


def _string_call(source, name: str) -> str:
    value = _call(source, name)
    return "" if value is None else str(value)


def _stable_object_id(source) -> str | None:
    unique_id = _call(source, "uniqueId")
    if unique_id is None:
        return None
    value = str(unique_id)
    return value or None


def _qt_class_name(source) -> str:
    meta_object = _call(source, "metaObject")
    return _string_call(meta_object, "className")
