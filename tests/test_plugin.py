from krita_brush_replay import plugin


class FakeDockBase:
    pass


class FakeKritaApi:
    dock_widget_base = FakeDockBase

    def __init__(self) -> None:
        self.registration: tuple[object, str, type] | None = None

    def application(self):
        raise AssertionError("an explicit application should be used")

    def register_dock(self, application, identifier: str, dock_type: type) -> None:
        self.registration = (application, identifier, dock_type)


def test_register_plugin_registers_lazy_dock_without_importing_qt() -> None:
    application = object()
    api = FakeKritaApi()

    result = plugin.register_plugin(krita_instance=application, krita_api=api)

    assert result
    assert api.registration is not None
    registered_application, identifier, dock_type = api.registration
    assert registered_application is application
    assert identifier == plugin.DOCK_FACTORY_ID
    assert issubclass(dock_type, FakeDockBase)
