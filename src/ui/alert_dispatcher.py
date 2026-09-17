"""
alert_dispatcher.py
-------------------
Decide **por qué canal** sale cada alerta confirmada por el FSM.

El problema que resuelve
------------------------
No todas las alertas de este sistema tienen la misma urgencia, y tratarlas
igual es un error de diseño en ambas direcciones:

  - Una cabeza adelantada durante 5 s es un riesgo **acumulativo**. Interrumpir
    al usuario con un modal bloqueante cada 30 s por eso es contraproducente:
    genera fatiga de alertas y termina con la herramienta desinstalada. Merece
    un aviso periférico.
  - La somnolencia por PERCLOS es un riesgo **agudo**. Si alguien lleva el 15%
    del último minuto con los ojos cerrados, un toast que se desvanece en
    cinco segundos es insuficiente: ahí interrumpir sí está justificado.

Por eso el canal se elige por tipo de alerta y es configurable desde
`config/thresholds.json` → `alerts`, sin tocar código:

    "alerts": {
      "mode": "auto",
      "blocking_alert_types": ["drowsiness"],
      "toast_enabled": true
    }

Modos
-----
    "toast"   Todo por notificación discreta. Nunca bloquea.
    "modal"   Todo por modal bloqueante. Reproduce el comportamiento original
              descrito en el Capítulo III de la tesis; se conserva para poder
              comparar ambos regímenes en la validación.
    "auto"    Toast por defecto; modal solo para los tipos listados en
              `blocking_alert_types`. Es el valor por defecto.

Relación con el Capítulo III
----------------------------
La tesis describe "alerta modal bloqueante" como decisión de diseño. Este
módulo **no la elimina**: la convierte en uno de tres regímenes seleccionables,
de modo que el cambio sea una evolución justificada y medible (el modo "modal"
sigue disponible para contrastar) y no una desviación silenciosa respecto al
documento.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

MODE_TOAST = "toast"
MODE_MODAL = "modal"
MODE_AUTO = "auto"
_VALID_MODES = (MODE_TOAST, MODE_MODAL, MODE_AUTO)

# Tipos que interrumpen en modo "auto" si el JSON no dice otra cosa.
# Solo la somnolencia: es el único indicador de este sistema que señala un
# estado agudo (microsueño) y no un hábito postural acumulativo.
DEFAULT_BLOCKING_TYPES = ("drowsiness",)


class AlertDispatcher:
    """
    Enruta cada AlertEvent al canal adecuado.

    Uso::

        dispatcher = AlertDispatcher(
            thresholds=thresholds,
            toast=toast_notifier,
            show_modal=lambda ev: page.run_thread(
                lambda: alert_notifier.show(ev)),
        )
        dispatcher.dispatch(alert_event)   # desde el hilo de inferencia
    """

    def __init__(self,
                 thresholds: dict,
                 toast=None,
                 show_modal: Optional[Callable] = None,
                 on_any_alert: Optional[Callable] = None):
        """
        Parameters
        ----------
        thresholds:
            Diccionario completo de config/thresholds.json.
        toast:
            Instancia de ToastNotifier (o None para desactivar el canal).
        show_modal:
            Callable que muestra el modal bloqueante. **Debe encargarse de
            saltar al hilo de la UI**: el dispatcher se invoca desde el hilo
            de inferencia.
        on_any_alert:
            Se llama con todo evento, sea cual sea el canal. Lo usa el panel
            de telemetría para dejar rastro visible de la alerta en la propia
            interfaz, que es lo que el usuario consulta al volver al escritorio.
        """
        alerts_cfg = thresholds.get("alerts", {})

        mode = str(alerts_cfg.get("mode", MODE_AUTO)).lower()
        if mode not in _VALID_MODES:
            logger.warning(
                "alerts.mode='%s' no es válido (%s); usando '%s'.",
                mode, ", ".join(_VALID_MODES), MODE_AUTO)
            mode = MODE_AUTO
        self.mode = mode

        blocking: Sequence[str] = alerts_cfg.get(
            "blocking_alert_types", DEFAULT_BLOCKING_TYPES)
        self.blocking_types = {str(t) for t in blocking}

        self._toast = toast
        self._show_modal = show_modal
        self._on_any_alert = on_any_alert

        logger.info(
            "AlertDispatcher: modo='%s', bloquean=%s, toast=%s",
            self.mode,
            sorted(self.blocking_types) or "ninguna",
            "sí" if toast is not None else "no",
        )

    # ------------------------------------------------------------------

    def is_blocking(self, alert_type_value: str) -> bool:
        """True si este tipo de alerta debe mostrarse como modal bloqueante."""
        if self.mode == MODE_MODAL:
            return True
        if self.mode == MODE_TOAST:
            return False
        return alert_type_value in self.blocking_types

    def dispatch(self, alert_event) -> str:
        """
        Envía la alerta por el canal que le corresponde.

        Se llama desde el hilo de inferencia, así que **no debe bloquear**:
        el toast se despacha a su propio hilo y el modal se reenvía al hilo
        de la UI por el callable inyectado.

        Returns
        -------
        str
            Canal utilizado: "modal", "toast" o "none". El valor se usa en los
            tests y en el registro de la sesión.
        """
        alert_type = alert_event.alert_type.value

        # El rastro en la interfaz se deja siempre, incluso si la notificación
        # no llega a verse: es lo que el usuario revisa al volver al panel.
        if self._on_any_alert is not None:
            try:
                self._on_any_alert(alert_event)
            except Exception:
                logger.exception("Error en el callback de alerta del panel.")

        if self.is_blocking(alert_type):
            if self._show_modal is not None:
                try:
                    self._show_modal(alert_event)
                    return MODE_MODAL
                except Exception:
                    logger.exception(
                        "No se pudo mostrar el modal; se intenta con toast.")
            # Sin modal disponible, el toast es mejor que nada.

        if self._toast is not None:
            self._toast.show(alert_event)
            return MODE_TOAST

        logger.debug("Alerta %s sin canal de salida disponible.", alert_type)
        return "none"
