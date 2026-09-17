"""
test_background_alerts.py
-------------------------
Pruebas del monitoreo en segundo plano y del enrutado de alertas.

Qué se cubre aquí y qué no
--------------------------
Se cubre la **lógica de decisión**: qué canal recibe cada alerta, qué pasa
cuando un canal falla, y que la visibilidad de la ventana no altere la
medición. No se cubre que un toast aparezca realmente en pantalla ni que el
icono de bandeja se dibuje: eso depende del escritorio del usuario y no es
automatizable sin un entorno gráfico real.

Ninguna prueba abre la cámara, lanza MediaPipe ni emite notificaciones reales.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertEvent, AlertType
from src.ui.alert_dispatcher import (
    AlertDispatcher, MODE_AUTO, MODE_MODAL, MODE_TOAST, DEFAULT_BLOCKING_TYPES,
)
from src.ui.toast_notifier import ToastNotifier, _TOAST_TITLES, _TOAST_ACTIONS
from src.ui.tray_icon import TrayIcon


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------

class FakeToast:
    """Sustituto de ToastNotifier que solo anota qué se le pidió mostrar."""

    def __init__(self):
        self.shown = []

    def show(self, alert_event):
        self.shown.append(alert_event.alert_type.value)


def make_event(alert_type: AlertType, duration: float = 6.0) -> AlertEvent:
    return AlertEvent(
        alert_type=alert_type,
        metric_value=35.0,
        threshold=30.0,
        duration_sec=duration,
        message=f"prueba {alert_type.value}",
    )


ALL_TYPES = list(AlertType)


# ---------------------------------------------------------------------------
# AlertDispatcher — elección de canal
# ---------------------------------------------------------------------------

class TestAlertDispatcherRouting:

    def test_auto_sends_posture_to_toast_not_modal(self):
        """
        Una postura de riesgo es un riesgo acumulativo: debe avisar sin
        interrumpir. Es el requisito central del monitoreo en segundo plano.
        """
        toast, modales = FakeToast(), []
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_AUTO}},
            toast=toast,
            show_modal=modales.append,
        )
        canal = d.dispatch(make_event(AlertType.CERVICAL_ANGLE))

        assert canal == MODE_TOAST
        assert toast.shown == ["cervical_angle"]
        assert modales == [], "la postura nunca debe abrir un modal bloqueante"

    def test_auto_sends_drowsiness_to_modal(self):
        """
        La somnolencia sí es aguda (microsueño): ahí interrumpir está
        justificado y el toast se considera insuficiente.
        """
        toast, modales = FakeToast(), []
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_AUTO}},
            toast=toast,
            show_modal=modales.append,
        )
        canal = d.dispatch(make_event(AlertType.DROWSINESS))

        assert canal == MODE_MODAL
        assert [e.alert_type for e in modales] == [AlertType.DROWSINESS]
        assert toast.shown == []

    @pytest.mark.parametrize("alert_type", ALL_TYPES)
    def test_mode_toast_never_blocks(self, alert_type):
        toast, modales = FakeToast(), []
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_TOAST}},
            toast=toast,
            show_modal=modales.append,
        )
        assert d.dispatch(make_event(alert_type)) == MODE_TOAST
        assert modales == []

    @pytest.mark.parametrize("alert_type", ALL_TYPES)
    def test_mode_modal_reproduces_original_behaviour(self, alert_type):
        """El modo 'modal' debe reproducir el Capítulo III tal cual, para poder
        contrastar ambos regímenes en la validación."""
        toast, modales = FakeToast(), []
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_MODAL}},
            toast=toast,
            show_modal=modales.append,
        )
        assert d.dispatch(make_event(alert_type)) == MODE_MODAL
        assert toast.shown == []

    def test_blocking_types_are_configurable(self):
        toast, modales = FakeToast(), []
        d = AlertDispatcher(
            thresholds={"alerts": {
                "mode": MODE_AUTO,
                "blocking_alert_types": ["eye_fatigue"],
            }},
            toast=toast,
            show_modal=modales.append,
        )
        assert d.dispatch(make_event(AlertType.EYE_FATIGUE)) == MODE_MODAL
        # La somnolencia deja de bloquear porque la config la excluyó.
        assert d.dispatch(make_event(AlertType.DROWSINESS)) == MODE_TOAST

    def test_invalid_mode_falls_back_to_auto(self):
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": "modo_inventado"}},
            toast=FakeToast(),
            show_modal=lambda _: None,
        )
        assert d.mode == MODE_AUTO

    def test_defaults_without_alerts_section(self):
        """Un thresholds.json antiguo (sin sección 'alerts') debe seguir
        funcionando con el comportamiento por defecto."""
        d = AlertDispatcher(thresholds={}, toast=FakeToast(),
                            show_modal=lambda _: None)
        assert d.mode == MODE_AUTO
        assert d.blocking_types == set(DEFAULT_BLOCKING_TYPES)


class TestAlertDispatcherResilience:

    def test_modal_failure_falls_back_to_toast(self):
        """
        Si el modal falla (ventana oculta en la bandeja, por ejemplo), la
        alerta no puede perderse: debe salir por el otro canal.
        """
        toast = FakeToast()

        def modal_roto(_):
            raise RuntimeError("no hay ventana")

        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_MODAL}},
            toast=toast,
            show_modal=modal_roto,
        )
        assert d.dispatch(make_event(AlertType.DROWSINESS)) == MODE_TOAST
        assert toast.shown == ["drowsiness"]

    def test_panel_callback_failure_does_not_lose_the_alert(self):
        toast = FakeToast()

        def panel_roto(_):
            raise RuntimeError("panel caido")

        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_TOAST}},
            toast=toast,
            on_any_alert=panel_roto,
        )
        assert d.dispatch(make_event(AlertType.YAWN)) == MODE_TOAST
        assert toast.shown == ["yawn"]

    def test_panel_receives_every_alert_whatever_the_channel(self):
        """
        El rastro en el panel es lo que el usuario encuentra al volver al
        escritorio: debe registrarse aunque la alerta saliera por modal.
        """
        anotadas = []
        d = AlertDispatcher(
            thresholds={"alerts": {"mode": MODE_AUTO}},
            toast=FakeToast(),
            show_modal=lambda _: None,
            on_any_alert=lambda e: anotadas.append(e.alert_type.value),
        )
        d.dispatch(make_event(AlertType.CERVICAL_ANGLE))   # -> toast
        d.dispatch(make_event(AlertType.DROWSINESS))       # -> modal

        assert anotadas == ["cervical_angle", "drowsiness"]

    def test_no_channels_available_does_not_raise(self):
        d = AlertDispatcher(thresholds={}, toast=None, show_modal=None)
        assert d.dispatch(make_event(AlertType.CERVICAL_ANGLE)) == "none"


# ---------------------------------------------------------------------------
# ToastNotifier
# ---------------------------------------------------------------------------

class TestToastNotifier:

    @pytest.mark.parametrize("alert_type", ALL_TYPES)
    def test_every_alert_type_has_title_and_action(self, alert_type):
        """
        Un toast tiene poco espacio: cada tipo necesita su título corto y una
        única acción concreta. Si se añade un AlertType y se olvida el texto,
        esta prueba lo detecta.
        """
        assert alert_type.value in _TOAST_TITLES
        assert alert_type.value in _TOAST_ACTIONS
        assert _TOAST_ACTIONS[alert_type.value].strip()

    def test_disabled_notifier_does_nothing(self):
        respaldo = []
        t = ToastNotifier(fallback=respaldo.append, enabled=False)
        t.show(make_event(AlertType.CERVICAL_ANGLE))
        assert respaldo == []
        t.close()

    def test_fallback_used_when_no_native_toast(self, monkeypatch):
        """En Linux/macOS (sin winotify) la alerta debe seguir llegando."""
        monkeypatch.setattr("src.ui.toast_notifier._winotify_available",
                            lambda: False)
        respaldo = []
        t = ToastNotifier(fallback=respaldo.append)

        assert t.uses_native_toast is False
        t.show(make_event(AlertType.EYE_FATIGUE))
        assert [e.alert_type for e in respaldo] == [AlertType.EYE_FATIGUE]
        t.close()

    def test_broken_fallback_does_not_propagate(self, monkeypatch):
        """Un fallo al notificar nunca debe tumbar el hilo de inferencia: el
        evento ya está en la bitácora, que es el entregable."""
        monkeypatch.setattr("src.ui.toast_notifier._winotify_available",
                            lambda: False)

        def respaldo_roto(_):
            raise RuntimeError("sin ventana")

        t = ToastNotifier(fallback=respaldo_roto)
        t.show(make_event(AlertType.YAWN))   # no debe lanzar
        t.close()

    def test_show_after_close_does_not_raise(self, monkeypatch):
        monkeypatch.setattr("src.ui.toast_notifier._winotify_available",
                            lambda: True)
        t = ToastNotifier(fallback=None)
        t.close()
        t.show(make_event(AlertType.CERVICAL_ANGLE))   # executor ya apagado

    def test_close_is_idempotent(self):
        t = ToastNotifier(fallback=None)
        t.close()
        t.close()


# ---------------------------------------------------------------------------
# TrayIcon — sin escritorio real
# ---------------------------------------------------------------------------

class TestTrayIcon:

    def test_pause_toggle_reports_new_state(self):
        estados = []
        tray = TrayIcon(on_toggle_pause=estados.append)

        tray._handle_toggle_pause()
        tray._handle_toggle_pause()

        assert estados == [True, False], "debe alternar pausa/reanudar"
        assert tray.is_paused is False

    def test_show_and_quit_callbacks_fire(self):
        llamadas = []
        tray = TrayIcon(on_show=lambda: llamadas.append("show"),
                        on_quit=lambda: llamadas.append("quit"))
        tray._handle_show()
        tray._handle_quit()
        assert llamadas == ["show", "quit"]

    def test_broken_callback_does_not_propagate(self):
        """Un fallo en un handler de la bandeja no debe matar su hilo."""
        def roto():
            raise RuntimeError("boom")

        tray = TrayIcon(on_show=roto)
        tray._handle_show()   # no debe lanzar

    def test_unavailable_tray_degrades_quietly(self, monkeypatch):
        """Sin pystray (o sin bandeja en el escritorio) la app debe seguir
        funcionando como ventana normal."""
        monkeypatch.setattr("src.ui.tray_icon._tray_available", lambda: False)
        tray = TrayIcon()
        assert tray.is_available is False
        assert tray.start() is False
        tray.stop()   # idempotente y sin icono

    def test_set_paused_reflects_external_change(self):
        tray = TrayIcon()
        tray.set_paused(True)
        assert tray.is_paused is True
