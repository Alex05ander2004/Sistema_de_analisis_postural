"""
Pruebas de humo de los componentes de UI.

No renderizan nada: construyen los widgets de Flet y los actualizan con
métricas reales. Sirven para que un error de API de Flet (un icono que no
existe, un atributo renombrado entre versiones) salte aquí y no delante de un
participante del estudio, que es donde se descubrió la última vez que la
aplicación no arrancaba.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fusion.fusion_fsm import AlertEvent, AlertType, SensorMetrics
from src.ui.alert_notifier import _ALERT_COLORS, _PAUSE_INSTRUCTIONS, AlertNotifier
from src.ui.telemetry_panel import TelemetryPanel
from src.ui.video_feed import VideoFeed


_THRESHOLDS = {
    "postural": {"cervical_angle_max_deg": 30.0, "shoulder_asymmetry_max_deg": 10.0},
    "fatigue": {"ear_threshold": 0.21, "mouth_opening_threshold": 0.45},
    "camera": {"min_distance_m": 0.50, "max_distance_m": 0.70},
}


class _FakePage:
    """Doble mínimo de ft.Page para AlertNotifier."""

    def __init__(self):
        self.overlay = []
        self.updates = 0

    def update(self):
        self.updates += 1


# ---------------------------------------------------------------------------
# TelemetryPanel
# ---------------------------------------------------------------------------

class TestTelemetryPanel:

    def test_builds(self):
        assert TelemetryPanel(_THRESHOLDS).build() is not None

    def test_update_with_full_metrics(self):
        panel = TelemetryPanel(_THRESHOLDS)
        panel.build()
        panel.update(
            SensorMetrics(cervical_angle=12.3, shoulder_asymmetry=-4.2,
                          ear_avg=0.284, mouth_opening=0.123,
                          distance_m=0.62, perclos=0.08,
                          blink_rate_per_min=17.0),
            fps=31.4,
            timer_status={"cervical_elapsed_sec": 1.2, "cervical_window_sec": 5,
                          "shoulder_elapsed_sec": 0.0, "shoulder_window_sec": 8,
                          "ear_elapsed_sec": 0.0, "ear_window_sec": 3},
        )

    def test_update_without_detection(self):
        """Sin pose ni cara el panel no debe romperse."""
        panel = TelemetryPanel(_THRESHOLDS)
        panel.build()
        panel.update(SensorMetrics(pose_detected=False, face_detected=False,
                                   distance_m=None),
                     fps=0.0, timer_status={})

    def test_ear_shown_with_enough_precision(self):
        """
        El EAR se mueve en centésimas alrededor de 0.21. Con un solo decimal,
        0.15 y 0.24 se mostraban ambos como "0.2" y era imposible ver en
        pantalla si la métrica estaba cerca del umbral.
        """
        panel = TelemetryPanel(_THRESHOLDS)
        panel.build()
        panel.update(SensorMetrics(ear_avg=0.238), fps=30.0)
        assert panel._row_ear._value_text.value == "0.238"

    def test_mar_thresholds_follow_config(self):
        """
        Los umbrales del panel deben venir del JSON, no estar cableados: si no,
        el semáforo puede quedarse en verde mientras el FSM dispara la alerta.
        """
        thresholds = {**_THRESHOLDS,
                      "fatigue": {**_THRESHOLDS["fatigue"],
                                  "mouth_opening_threshold": 0.60}}
        panel = TelemetryPanel(thresholds)
        assert panel._row_mouth.alert_threshold == 0.60

    def test_distance_colour_reflects_recommended_range(self):
        panel = TelemetryPanel(_THRESHOLDS)
        panel.build()

        panel.update(SensorMetrics(distance_m=0.60), fps=30.0)
        in_range = panel._dist_text.color

        panel.update(SensorMetrics(distance_m=1.20), fps=30.0)
        out_of_range = panel._dist_text.color

        assert in_range != out_of_range
        assert panel._dist_text.value == "1.20 m"


# ---------------------------------------------------------------------------
# VideoFeed
# ---------------------------------------------------------------------------

class TestVideoFeed:

    def _frame(self):
        return np.full((480, 640, 3), 128, dtype=np.uint8)

    def test_builds(self):
        assert VideoFeed().build() is not None

    def test_encode_returns_base64(self):
        b64 = VideoFeed().encode(self._frame(), fps=30.0)
        assert isinstance(b64, str) and len(b64) > 100

    def test_encode_returns_none_for_missing_frame(self):
        assert VideoFeed().encode(None) is None

    def test_set_encoded_hides_placeholder_on_first_frame(self):
        feed = VideoFeed()
        feed.build()
        assert feed._placeholder.visible is not False
        feed.set_encoded(feed.encode(self._frame(), fps=30.0))
        assert feed._placeholder.visible is False
        assert feed.frame_count == 1

    def test_set_encoded_ignores_none(self):
        """
        Con el render limitado a la cadencia de la UI, el hilo de inferencia
        publica estados sin frame: la UI debe conservar el último.
        """
        feed = VideoFeed()
        feed.build()
        feed.set_encoded(feed.encode(self._frame(), fps=30.0))
        previous = feed._image.src_base64
        feed.set_encoded(None)
        assert feed._image.src_base64 == previous
        assert feed.frame_count == 1

    def test_overlay_modifies_frame_in_place(self):
        """El overlay del FPS ya no copia el frame completo."""
        feed = VideoFeed()
        frame = self._frame()
        feed.encode(frame, fps=30.0)
        assert frame[0, 0].tolist() != [128, 128, 128], (
            "El overlay debe escribirse sobre el frame recibido"
        )
        assert frame[400, 600].tolist() == [128, 128, 128], (
            "Fuera del recuadro del overlay el frame no debe tocarse"
        )


# ---------------------------------------------------------------------------
# AlertNotifier
# ---------------------------------------------------------------------------

class TestAlertNotifier:

    def _event(self, alert_type=AlertType.CERVICAL_ANGLE):
        return AlertEvent(alert_type=alert_type, metric_value=35.0,
                          threshold=30.0, duration_sec=5.4,
                          message="mensaje de prueba")

    @pytest.mark.parametrize("alert_type", list(AlertType))
    def test_every_alert_type_has_colour_and_instructions(self, alert_type):
        """
        Un AlertType sin entrada en las tablas caería al texto genérico
        "Tómate un descanso de 5 minutos", que no dice nada al usuario.
        """
        assert alert_type.value in _ALERT_COLORS
        assert alert_type.value in _PAUSE_INSTRUCTIONS
        assert len(_PAUSE_INSTRUCTIONS[alert_type.value]) >= 3

    @pytest.mark.parametrize("alert_type", list(AlertType))
    def test_show_builds_a_modal(self, alert_type):
        page = _FakePage()
        notifier = AlertNotifier(page)
        notifier.show(self._event(alert_type))
        assert notifier.is_open
        assert len(page.overlay) == 1

    def test_second_alert_does_not_stack_a_modal(self):
        """
        REGRESIÓN — modal huérfano bloqueando la ventana.

        Postura y fatiga pueden confirmarse en el mismo frame. Antes, el
        segundo `show()` sustituía `self._dialog` con el primer modal todavía
        abierto: el botón "Entendido" cerraba solo el último y el anterior
        quedaba bloqueando la aplicación para siempre.
        """
        page = _FakePage()
        notifier = AlertNotifier(page)
        notifier.show(self._event(AlertType.CERVICAL_ANGLE))
        notifier.show(self._event(AlertType.EYE_FATIGUE))
        assert len(page.overlay) == 1

    def test_dismiss_closes_without_removing_the_dialog(self):
        """
        REGRESIÓN — recuadro vacío que no se puede cerrar (sesión 2026-09-24).

        Al pulsar "Entendido", el diálogo se marcaba cerrado Y se retiraba del
        overlay en la misma actualización. Flet solo enviaba "control
        eliminado", el cliente nunca recibía open = False y la ruta del
        diálogo quedaba huérfana en Flutter: un recuadro vacío en mitad de la
        pantalla. El cierre correcto es open = False con el control todavía en
        el árbol, igual que hace `Page.close()` en Flet 0.24.
        """
        page = _FakePage()
        notifier = AlertNotifier(page)
        notifier.show(self._event())
        dialog = notifier._dialog

        notifier._dialog.actions[0].on_click(None)

        assert dialog.open is False, "el cierre debe enviar open = False"
        assert dialog in page.overlay, (
            "retirar el diálogo del árbol al cerrarlo deja la ruta huérfana")
        assert not notifier.is_open

    def test_overlay_stays_bounded_across_many_alerts(self):
        """
        REGRESIÓN — crecimiento sin límite de `page.overlay`.

        Flet serializa el overlay entero en cada `page.update()`. Con el
        monitoreo corriendo toda la jornada, una entrada por alerta haría
        crecer el árbol sin parar. Se reutiliza siempre el mismo diálogo.
        """
        page = _FakePage()
        notifier = AlertNotifier(page)

        notifier.show(self._event())
        first_dialog = notifier._dialog
        for _ in range(50):
            notifier._dialog.actions[0].on_click(None)
            notifier.show(self._event())

        assert len(page.overlay) == 1
        assert notifier._dialog is first_dialog, "debe reutilizarse el diálogo"

    def test_reopened_dialog_shows_the_new_alert(self):
        """El diálogo reutilizado debe mostrar la alerta nueva, no la anterior."""
        page = _FakePage()
        notifier = AlertNotifier(page)

        notifier.show(self._event(AlertType.CERVICAL_ANGLE))
        notifier._dialog.actions[0].on_click(None)
        notifier.show(self._event(AlertType.DROWSINESS))

        header_text = notifier._dialog.content.controls[0].content.controls[0].value
        assert "SOMNOLENCIA" in header_text
        assert notifier.is_open

    def test_dismiss_twice_is_harmless(self):
        page = _FakePage()
        received = []
        notifier = AlertNotifier(page, on_dismissed=received.append)
        notifier.show(self._event())
        notifier._dialog.actions[0].on_click(None)
        notifier._dialog.actions[0].on_click(None)
        assert len(received) == 1, "el callback no debe dispararse dos veces"

    def test_echo_of_own_dismiss_does_not_close_a_new_modal(self):
        """
        El evento on_dismiss del cliente puede llegar como eco del cierre que
        acabamos de hacer. Si entretanto se abrió un modal nuevo, ese eco no
        debe cerrarlo en el lado Python: el botón dejaría de funcionar y el
        recuadro volvería a quedarse atascado.
        """
        page = _FakePage()
        notifier = AlertNotifier(page)
        notifier.show(self._event(AlertType.CERVICAL_ANGLE))
        notifier._dialog.actions[0].on_click(None)
        notifier.show(self._event(AlertType.DROWSINESS))

        notifier._on_client_dismiss(None)     # eco tardío del primer cierre

        assert notifier.is_open, "el eco no debe cerrar el modal nuevo"

    def test_client_side_dismiss_resyncs_state(self, monkeypatch):
        """
        Si el cliente cierra el diálogo por su cuenta, el lado Python debe
        enterarse: si no, el guardia de show() suprimiría todas las alertas
        siguientes sin nada visible en pantalla.
        """
        import src.ui.alert_notifier as mod
        page = _FakePage()
        notifier = AlertNotifier(page)
        notifier.show(self._event())

        monkeypatch.setattr(mod, "_DISMISS_ECHO_SEC", 0.0)
        notifier._on_client_dismiss(None)
        assert not notifier.is_open

        notifier.show(self._event(AlertType.DROWSINESS))
        assert notifier.is_open, "tras resincronizar debe poder mostrarse otra"

    def test_on_dismissed_callback_receives_the_event(self):
        page = _FakePage()
        received = []
        notifier = AlertNotifier(page, on_dismissed=received.append)
        event = self._event()
        notifier.show(event)
        notifier._dialog.actions[0].on_click(None)
        assert received == [event]
