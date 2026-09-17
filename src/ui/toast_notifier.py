"""
toast_notifier.py
-----------------
Notificación discreta del sistema operativo para las alertas del FSM.

Por qué existe
--------------
El diseño original (`alert_notifier.py`) es un modal bloqueante **dentro** de
la ventana de Flet. Eso funciona si el usuario está mirando la app, pero el
caso de uso real es el contrario: el sistema corre en segundo plano mientras
la persona trabaja en otra aplicación. Un modal de Flet en esa situación
falla de tres formas:

  1. Es invisible: se pinta en una ventana que nadie está mirando.
  2. Queda abierto indefinidamente y, por el guardia anti-solapamiento de
     `AlertNotifier.show()`, **bloquea la visualización de todas las alertas
     posteriores** de la sesión.
  3. Cuando el usuario vuelve, se encuentra un modal rancio sobre una postura
     de hace veinte minutos.

Una notificación del sistema (toast) se muestra por encima de cualquier
aplicación, se cierra sola, no roba el foco del teclado y queda archivada en
el Centro de Actividades de Windows. Es el canal correcto para un aviso
postural, que es informativo y no urgente.

Sobre la fatiga de alertas
--------------------------
La literatura de seguridad de sistemas es consistente en que una alerta que
interrumpe se desatiende rápido. El propio `config/thresholds.json` ya lo
anota para `alert_cooldown_sec`: con un cooldown de 30 s y postura de riesgo
sostenida, un modal bloqueante produce dos interrupciones por minuto, que es
la receta clásica para que el usuario desinstale la herramienta. El toast
degrada esa interrupción a un aviso periférico, que es lo que corresponde a
un riesgo acumulativo y no agudo.

Estrategia de degradación
-------------------------
    Windows + winotify  ->  toast nativo del sistema
    cualquier otro caso ->  callback de respaldo (la app lo resuelve con un
                            aviso dentro de su propia ventana)

El respaldo importa porque el README declara soporte para Ubuntu y macOS, y
`winotify` es exclusivo de Windows.

Coste y bloqueo
---------------
`winotify` lanza un proceso de PowerShell para emitir el toast, lo que tarda
del orden de cientos de milisegundos. Llamarlo desde el hilo de inferencia
frenaría el pipeline y hundiría el FPS justo en el instante de la alerta —
precisamente el momento que el Capítulo IV mide. Por eso cada toast se
despacha a un hilo aparte de un solo worker.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ICON_PATH = PROJECT_ROOT / "assets" / "icon.ico"

# El app_id es lo que Windows muestra como emisor del toast y lo que usa para
# agrupar las notificaciones en el Centro de Actividades.
_APP_ID = "Monitor Postural y Fatiga"

# Título corto por tipo de alerta. El toast tiene poco espacio: el título debe
# decir qué pasa sin que haga falta leer el cuerpo.
_TOAST_TITLES = {
    "cervical_angle":     "Cabeza adelantada",
    "shoulder_asymmetry": "Hombros desnivelados",
    "eye_fatigue":        "Fatiga ocular",
    "yawn":               "Signos de cansancio",
    "drowsiness":         "Somnolencia detectada",
}

# Una sola acción concreta por alerta. La lista completa de pausa activa vive
# en el modal; meterla en un toast lo vuelve ilegible.
_TOAST_ACTIONS = {
    "cervical_angle":     "Endereza la cabeza sobre los hombros.",
    "shoulder_asymmetry": "Nivela los hombros y relájalos.",
    "eye_fatigue":        "Regla 20-20-20: mira a 6 m durante 20 s.",
    "yawn":               "Levántate y camina un par de minutos.",
    "drowsiness":         "Interrumpe la tarea y descansa.",
}


def _winotify_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winotify  # noqa: F401
        return True
    except ImportError:
        return False


class ToastNotifier:
    """
    Emisor de notificaciones discretas del sistema operativo.

    Uso::

        toast = ToastNotifier(fallback=lambda ev: panel.mostrar_aviso(ev))
        toast.show(alert_event)      # no bloquea: despacha a otro hilo
        toast.close()                # al cerrar la aplicación
    """

    def __init__(self, fallback: Optional[Callable] = None,
                 enabled: bool = True):
        """
        Parameters
        ----------
        fallback:
            Se invoca con el AlertEvent cuando no hay toast nativo disponible
            (Linux, macOS, o Windows sin winotify). La app lo usa para mostrar
            un aviso dentro de su propia ventana.
        enabled:
            False desactiva el canal por completo (`alerts.toast_enabled` en
            config/thresholds.json).
        """
        self._fallback = fallback
        self._enabled = enabled
        self._native = _winotify_available() if enabled else False

        # Un solo worker: los toasts deben salir en orden y nunca en paralelo,
        # y así una ráfaga de alertas no abre N procesos de PowerShell a la vez.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="toast") if enabled else None

        if not enabled:
            logger.info("ToastNotifier deshabilitado por configuración.")
        elif self._native:
            logger.info("ToastNotifier: notificaciones nativas de Windows.")
        else:
            logger.info(
                "ToastNotifier: sin toast nativo en esta plataforma (%s); "
                "se usará el aviso dentro de la ventana.", sys.platform)

    # ------------------------------------------------------------------

    @property
    def uses_native_toast(self) -> bool:
        """True si las alertas salen como notificación del sistema."""
        return self._native

    def show(self, alert_event) -> None:
        """
        Emite la notificación. **No bloquea**: puede llamarse desde el hilo de
        inferencia sin afectar al FPS.
        """
        if not self._enabled:
            return

        if not self._native:
            self._run_fallback(alert_event)
            return

        # `close()` pone el executor a None. Una alerta puede confirmarse en el
        # instante exacto en que el usuario cierra la aplicación, así que esto
        # es una carrera real, no una hipótesis: sin la comprobación, el hilo
        # de inferencia moriría con AttributeError durante el apagado.
        executor = self._executor
        if executor is None:
            logger.debug("Toast descartado: el emisor ya está cerrado.")
            return

        try:
            executor.submit(self._show_native, alert_event)
        except RuntimeError:
            # El executor se apagó entre la comprobación y el submit.
            logger.debug("Toast descartado: el emisor se cerró durante el envío.")

    # ------------------------------------------------------------------

    def _show_native(self, alert_event) -> None:
        """Emite el toast de Windows. Corre en el hilo del executor."""
        try:
            from winotify import Notification, audio

            alert_type = alert_event.alert_type.value
            title = _TOAST_TITLES.get(alert_type, "Alerta postural")
            action = _TOAST_ACTIONS.get(alert_type, "Tómate una pausa activa.")

            toast = Notification(
                app_id=_APP_ID,
                title=title,
                msg=f"{action}\nDetectado durante {alert_event.duration_sec:.0f} s.",
                duration="short",
                icon=str(_ICON_PATH) if _ICON_PATH.exists() else "",
            )
            # Sonido suave y sin bucle: debe notarse sin sobresaltar. Un toast
            # silencioso pasa desapercibido justo cuando el usuario está
            # concentrado, que es cuando más falta hace el aviso.
            toast.set_audio(audio.Default, loop=False)
            toast.show()

            logger.debug("Toast emitido: %s", alert_type)
        except Exception:
            # Nunca dejar que un fallo de notificación tumbe el monitoreo: el
            # evento ya está registrado en la bitácora, que es el entregable.
            logger.exception("No se pudo emitir el toast; usando respaldo.")
            self._run_fallback(alert_event)

    def _run_fallback(self, alert_event) -> None:
        if self._fallback is None:
            return
        try:
            self._fallback(alert_event)
        except Exception:
            logger.exception("Error en el aviso de respaldo.")

    def close(self) -> None:
        """Cierra el emisor. Idempotente."""
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
