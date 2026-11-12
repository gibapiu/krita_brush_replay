"""Krita context and captured-input adapter."""

from __future__ import annotations

from collections.abc import Callable

from krita_brush_replay.domain.brush_override import BrushChoices
from krita_brush_replay.domain.recording import (
    BrushState,
    ContextRef,
    RecordingSession,
)
from krita_brush_replay.infrastructure.krita_brush_state import KritaBrushStateAdapter
from krita_brush_replay.infrastructure.krita_replay import KritaReplayer, QtScheduler
from krita_brush_replay.infrastructure.qt_facade import QtCore
from krita_brush_replay.infrastructure.synthetic_input import (
    CanvasInputTarget,
    SyntheticInputReplayer,
)


class KritaHost:
    """Read Krita state, map captured input, and delegate stroke replay."""

    def __init__(self, krita_instance=None, *, replayer_factory=KritaReplayer) -> None:
        self._krita = krita_instance if krita_instance is not None else _krita_instance()
        self._brush_settings = KritaBrushStateAdapter(self._krita)
        self._canvas_target = None
        scheduler = QtScheduler()
        self._replayer = replayer_factory(
            self.active_context,
            scheduler=scheduler,
            synthetic_input=SyntheticInputReplayer(self.replay_input_target, scheduler),
        )

    def active_context(self) -> ContextRef | None:
        target = self._active_target()
        return None if target is None else target[2]

    def _active_target(self) -> tuple[object, object, ContextRef] | None:
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
        return document, node, ContextRef(document_id, layer_id)

    def is_canvas_target(self, target) -> bool:
        window = _call(self._krita, "activeWindow")
        qwindow = _call(window, "qwindow")
        target_window = _call(target, "window")
        if qwindow is None or target_window != qwindow:
            return False
        current = target
        while current is not None and current != qwindow:
            class_name = _qt_class_name(current).lower()
            object_name = _string_call(current, "objectName").lower()
            if "canvas" in class_name or "openglwidget" in class_name or object_name == "canvas":
                self._canvas_target = target
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
        transform = _widget_to_image_transform(view)

        def to_image(position: tuple[float, float]) -> tuple[float, float]:
            image_position = transform.map(QtCore.QPointF(*position))
            return float(image_position.x()), float(image_position.y())

        return to_image

    def invalidate_canvas_target(self) -> None:
        self._canvas_target = None

    def replay_input_target(self) -> CanvasInputTarget:
        """Resolve one stable active-canvas receiver and coordinate mapper."""

        window = _call(self._krita, "activeWindow")
        qwindow = _call(window, "qwindow")
        view = _call(window, "activeView")
        receiver = self._canvas_target
        if (
            qwindow is None
            or view is None
            or receiver is None
            or _call(receiver, "window") != qwindow
        ):
            raise RuntimeError(
                "the active canvas input target is unavailable; record a stroke on it first"
            )
        widget_to_image = _widget_to_image_transform(view)
        image_to_widget, invertible = widget_to_image.inverted()
        if not invertible:
            raise RuntimeError("the image transform is not invertible")

        def to_widget(position: tuple[float, float]) -> tuple[float, float]:
            widget_position = image_to_widget.map(QtCore.QPointF(*position))
            return float(widget_position.x()), float(widget_position.y())

        def is_valid() -> bool:
            current_window = _call(self._krita, "activeWindow")
            current_view = _call(current_window, "activeView")
            try:
                current_transform = _widget_to_image_transform(current_view)
            except RuntimeError:
                return False
            return (
                self._canvas_target is receiver
                and _call(current_window, "qwindow") == qwindow
                and _call(receiver, "window") == qwindow
                and current_transform == widget_to_image
                and _bool_call(receiver, "isVisible", True)
                and _bool_call(receiver, "isEnabled", True)
            )

        def focus() -> None:
            setter = getattr(receiver, "setFocus", None)
            if not callable(setter):
                raise RuntimeError("the active canvas cannot receive focus")
            reasons = getattr(QtCore.Qt, "FocusReason", QtCore.Qt)
            reason = getattr(reasons, "OtherFocusReason", None)
            setter() if reason is None else setter(reason)

        target = CanvasInputTarget(receiver, to_widget, is_valid, focus)
        if not target.is_valid():
            raise RuntimeError("the active canvas input target is unavailable")
        return target

    def capture_brush_state(self) -> BrushState:
        view = self._active_view()
        if view is None:
            raise RuntimeError("no active Krita view")
        return self._brush_settings.capture(view)

    def brush_choices(self) -> BrushChoices:
        return self._brush_settings.brush_choices()

    def replay(
        self,
        session: RecordingSession,
        on_complete: Callable[[], None],
        on_error: Callable[[Exception], None],
        *,
        use_original_settings: bool,
    ) -> None:
        active_target = self._active_target()
        if active_target is None:
            on_error(RuntimeError("select a writable paint layer first"))
            return
        document, node, target = active_target

        view = self._active_view()
        if view is None:
            on_error(RuntimeError("no active Krita view"))
            return
        try:
            brush_settings = self._brush_settings.prepare_replay(
                view,
                tuple(stroke.brush_state for stroke in session.strokes)
                if use_original_settings
                else (),
            )
        except Exception as error:
            on_error(error)
            return

        def brush_settings_drifted() -> bool:
            return self._brush_settings.capture(view) != brush_settings.saved_state

        def apply_replay_state(state: BrushState) -> None:
            if use_original_settings:
                brush_settings.apply(state)
            elif brush_settings_drifted():
                # A first synthetic pen hover can load the pen's saved preset.
                brush_settings.restore()

        finished = False

        def finish(error: Exception | None = None) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            try:
                if use_original_settings or brush_settings_drifted():
                    brush_settings.restore()
            except Exception as restore_error:
                if error is None:
                    error = RuntimeError(
                        f"replay finished but brush settings could not be restored: {restore_error}"
                    )
                else:
                    error = RuntimeError(
                        f"{error}; brush settings could not be restored: {restore_error}"
                    )
            if error is None:
                on_complete()
            else:
                on_error(error)

        try:
            self._replayer.replay(
                document,
                node,
                session,
                target,
                lambda: finish(),
                finish,
                apply_brush_state=apply_replay_state,
            )
        except Exception as error:
            finish(error)

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


def _bool_call(source, name: str, default: bool) -> bool:
    value = _call(source, name)
    return default if value is None else bool(value)


def _widget_to_image_transform(view):
    canvas_transform = _call(view, "flakeToCanvasTransform")
    image_transform = _call(view, "flakeToImageTransform")
    if canvas_transform is None or image_transform is None:
        raise RuntimeError("Krita did not provide canvas transforms")
    widget_to_flake, invertible = canvas_transform.inverted()
    if not invertible:
        raise RuntimeError("the canvas transform is not invertible")
    return widget_to_flake * image_transform
