"""Krita 5/6 plugin-registration compatibility boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KritaFacade:
    application_type: object
    dock_widget_base: type
    dock_widget_factory_type: type
    dock_right: object

    @classmethod
    def from_module(cls, module: object) -> KritaFacade:
        application_type = _required(module, "Krita")
        dock_widget_base = _required(module, "DockWidget")
        factory_type = _required(module, "DockWidgetFactory")
        factory_base = _required(module, "DockWidgetFactoryBase")
        positions = getattr(factory_base, "DockPosition", factory_base)
        return cls(
            application_type=application_type,
            dock_widget_base=dock_widget_base,
            dock_widget_factory_type=factory_type,
            dock_right=_required(positions, "DockRight"),
        )

    def application(self):
        return self.application_type.instance()

    def register_dock(self, application, identifier: str, dock_type: type) -> None:
        factory = self.dock_widget_factory_type(identifier, self.dock_right, dock_type)
        application.addDockWidgetFactory(factory)


def load_krita() -> KritaFacade | None:
    try:
        import krita
    except ModuleNotFoundError as error:
        if error.name != "krita":
            raise
        return None
    return KritaFacade.from_module(krita)


def _required(source: object, name: str):
    try:
        return getattr(source, name)
    except AttributeError as error:
        raise RuntimeError(f"Krita Python API does not provide {name!r}") from error
