from types import SimpleNamespace

from krita_brush_replay.observability.context import INPUT_SETTINGS, KritaDiagnosticContext


def test_context_reads_only_relevant_public_settings() -> None:
    reads = []

    def read_setting(group, key, default):
        reads.append((group, key))
        return "0,0;0.5,0.8;1,1;" if key == "tabletPressureCurve" else default

    application = SimpleNamespace(
        version=lambda: "5.4.0",
        readSetting=read_setting,
        action=lambda name: SimpleNamespace(isChecked=lambda: True),
    )

    context = KritaDiagnosticContext(application).snapshot()

    assert context["krita_version"] == "5.4.0"
    assert context["pressure_action_checked"] is True
    assert context["input_settings"]["tabletPressureCurve"] == "0,0;0.5,0.8;1,1;"
    assert reads == [("", key) for key in INPUT_SETTINGS]


def test_unavailable_context_remains_exportable() -> None:
    context = KritaDiagnosticContext(object()).snapshot()

    assert "context_error" in context
    assert "qt_version" in context
