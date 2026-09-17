"""
tray_icon.py
------------
Icono en la bandeja del sistema, para que el monitoreo pueda quedarse
corriendo toda la jornada sin ocupar una ventana.

Por qué existe
--------------
El sistema mide riesgos que se acumulan en ventanas de 5-8 s pero que solo
tienen sentido clínico a lo largo de horas de trabajo. Obligar al usuario a
mantener visible una ventana de video para eso es contradictorio: le quita
pantalla justo mientras trabaja, y además el propio encuadre de la webcam
deja de ser representativo si la persona está mirando la app en vez de su
tarea real.

Con la bandeja, cerrar la ventana **oculta** la interfaz en lugar de matar el
proceso. La captura y la inferencia siguen corriendo en sus hilos, y el
usuario recupera el panel cuando quiere. Salir de verdad es una acción
explícita del menú.

Menú
----
    Abrir panel        -> restaura la ventana de Flet
    Pausar monitoreo   -> congela el pipeline (reunión, videollamada, otra
                          persona delante de la cámara)
    Salir              -> cierre ordenado: cierra la sesión en SQLite

La opción de pausa no es un adorno. Sin ella, el usuario que necesita dejar de
ser medido un rato solo puede cerrar la aplicación, y eso corta la sesión de
la bitácora en dos — con `ended_at` escrito a mitad de la jornada.

Dependencia
-----------
`pystray` es multiplataforma (Windows/Linux/macOS), pero la bandeja no existe
en todos los escritorios de Linux. Si no se puede crear el icono, la clase se
degrada a modo inactivo y la aplicación sigue funcionando como una ventana
normal: la bandeja es una comodidad, no un requisito del sistema de medición.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ICON_PNG = PROJECT_ROOT / "assets" / "icon.png"


def _tray_available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


class TrayIcon:
    """
    Icono de bandeja con menú, ejecutado en su propio hilo daemon.

    Uso::

        tray = TrayIcon(on_show=..., on_toggle_pause=..., on_quit=...)
        tray.start()
        tray.set_paused(True)     # refleja el estado en el menú
        tray.stop()
    """

    def __init__(self,
                 on_show: Optional[Callable[[], None]] = None,
                 on_toggle_pause: Optional[Callable[[bool], None]] = None,
                 on_quit: Optional[Callable[[], None]] = None,
                 title: str = "Monitor Postural y Fatiga"):
        """
        Parameters
        ----------
        on_show:
            Restaurar y traer al frente la ventana principal.
        on_toggle_pause:
            Recibe el nuevo estado de pausa (True = monitoreo detenido).
        on_quit:
            Cierre ordenado de la aplicación.
        title:
            Texto del tooltip del icono.
        """
        self._on_show = on_show
        self._on_toggle_pause = on_toggle_pause
        self._on_quit = on_quit
        self._title = title

        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._paused = False
        self._available = _tray_available()

        if not self._available:
            logger.info(
                "Bandeja no disponible (falta pystray/Pillow o el escritorio "
                "no la soporta); la app seguirá como ventana normal.")

    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True si el icono de bandeja pudo construirse."""
        return self._available

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> bool:
        """
        Arranca el icono en un hilo daemon. Devuelve True si quedó activo.

        pystray bloquea el hilo desde el que se llama a `run()`, así que va en
        su propio hilo: el event loop de Flet tiene que seguir siendo el hilo
        principal.
        """
        if not self._available or self._thread is not None:
            return self._available

        try:
            import pystray
            from PIL import Image

            image = (Image.open(_ICON_PNG) if _ICON_PNG.exists()
                     else self._placeholder_image())

            self._icon = pystray.Icon(
                name="monitor_postural",
                icon=image,
                title=self._title,
                menu=self._build_menu(),
            )
            self._thread = threading.Thread(
                target=self._icon.run, name="TrayIcon", daemon=True)
            self._thread.start()
            logger.info("Icono de bandeja activo.")
            return True
        except Exception:
            logger.exception("No se pudo crear el icono de bandeja.")
            self._available = False
            return False

    def _build_menu(self):
        import pystray

        def _pause_label(_item):
            return "Reanudar monitoreo" if self._paused else "Pausar monitoreo"

        return pystray.Menu(
            pystray.MenuItem("Abrir panel", self._handle_show, default=True),
            pystray.MenuItem(_pause_label, self._handle_toggle_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", self._handle_quit),
        )

    @staticmethod
    def _placeholder_image():
        """Icono mínimo si falta assets/icon.png, para no fallar por eso."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (10, 10, 22, 255))
        d = ImageDraw.Draw(img)
        d.ellipse([18, 10, 46, 38], fill="#00BCD4")
        d.rectangle([30, 36, 34, 52], fill="#00BCD4")
        return img

    # ------------------------------------------------------------------
    # Handlers del menú. Corren en el hilo de pystray, así que todo lo que
    # toque la UI de Flet debe reenviarse al hilo de Flet desde app_ui.
    # ------------------------------------------------------------------

    def _handle_show(self, icon=None, item=None) -> None:
        if self._on_show:
            try:
                self._on_show()
            except Exception:
                logger.exception("Error al restaurar la ventana desde la bandeja.")

    def _handle_toggle_pause(self, icon=None, item=None) -> None:
        self._paused = not self._paused
        if self._on_toggle_pause:
            try:
                self._on_toggle_pause(self._paused)
            except Exception:
                logger.exception("Error al cambiar el estado de pausa.")
        if self._icon is not None:
            self._icon.update_menu()
        logger.info("Monitoreo %s desde la bandeja.",
                    "PAUSADO" if self._paused else "reanudado")

    def _handle_quit(self, icon=None, item=None) -> None:
        logger.info("Salida solicitada desde la bandeja.")
        if self._on_quit:
            try:
                self._on_quit()
            except Exception:
                logger.exception("Error en el cierre desde la bandeja.")
        self.stop()

    # ------------------------------------------------------------------

    def set_paused(self, paused: bool) -> None:
        """Refleja en el menú un cambio de pausa originado fuera de la bandeja."""
        self._paused = paused
        if self._icon is not None:
            self._icon.update_menu()

    def notify(self, title: str, message: str) -> None:
        """
        Globo de notificación del propio icono de bandeja.

        Es el respaldo para plataformas donde `winotify` no existe; en Windows
        se prefiere el toast nativo, que es más rico y queda archivado en el
        Centro de Actividades.
        """
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            logger.debug("La bandeja de este sistema no soporta globos.")

    def stop(self) -> None:
        """Retira el icono. Idempotente."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                logger.debug("El icono de bandeja ya estaba detenido.")
            self._icon = None
        self._thread = None
