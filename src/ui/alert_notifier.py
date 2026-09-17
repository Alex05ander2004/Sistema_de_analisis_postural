"""
alert_notifier.py
-----------------
Componente de alerta modal bloqueante para Flet.

**Ya no es el canal por defecto.** Desde la incorporación del monitoreo en
segundo plano, `src/ui/alert_dispatcher.py` decide qué alerta sale por aquí y
cuál por notificación discreta del sistema (`toast_notifier.py`). Con la
configuración por defecto este modal se reserva para la **somnolencia**, que es
el único indicador agudo del sistema; el resto son riesgos acumulativos, donde
interrumpir al usuario cada 30 s produce fatiga de alertas.

El modal sigue siendo el comportamiento descrito en el Capítulo III de la
tesis, y puede reactivarse para todas las alertas con `alerts.mode: "modal"` en
`config/thresholds.json` — precisamente para poder contrastar ambos regímenes
en la sesión de validación. Ver `docs/validation_report.md` §3.5.

Limitación conocida (la razón de que exista el otro canal): este modal se pinta
**dentro** de la ventana de Flet, así que es invisible si el usuario está
trabajando en otra aplicación o la ventana está oculta en la bandeja.

Se activa cuando el FSM confirma una condición de riesgo.
Muestra un modal con:
  - Tipo de alerta (postural / fatiga)
  - Métrica que la disparó
  - Instrucción de pausa activa
  - Botón de confirmación (el usuario reconoce la alerta)

Diseño: oscuro / glassmorphism con colores semáforo por tipo de alerta.

Autor: Generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import logging
import threading

import flet as ft
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Paleta de colores por tipo de alerta
_ALERT_COLORS = {
    "cervical_angle":    {"bg": "#FF4444", "icon": "🔴", "label": "POSTURA CERVICAL"},
    "shoulder_asymmetry":{"bg": "#FF8800", "icon": "🟠", "label": "ASIMETRÍA DE HOMBROS"},
    "eye_fatigue":       {"bg": "#9B59B6", "icon": "👁️", "label": "FATIGA OCULAR"},
    "yawn":              {"bg": "#3498DB", "icon": "😴", "label": "BOSTEZO"},
    "drowsiness":        {"bg": "#8E44AD", "icon": "💤", "label": "SOMNOLENCIA (PERCLOS)"},
}

_PAUSE_INSTRUCTIONS = {
    "cervical_angle": [
        "Endereza la cabeza alineándola con la columna.",
        "Verifica que la pantalla esté a 50–70 cm de distancia, a la altura de tus ojos.",
        "Estiramiento cabeza-cuello: retracción cervical (mentón hacia atrás, sin inclinar) + 3 rotaciones suaves de cuello por lado.",
        "Si el malestar persiste, considera consultar a un fisioterapeuta o especialista.",
    ],
    "shoulder_asymmetry": [
        "Eleva ambos hombros y relájalos hacia abajo.",
        "Verifica que los codos estén a la altura del teclado.",
        "Estiramiento de hombros y cuello: retracción escapular + estiramiento de trapecio superior.",
        "Si el malestar persiste, considera consultar a un fisioterapeuta o especialista.",
    ],
    "eye_fatigue": [
        "Aplica la regla 20-20-20: mira a 6 metros durante 20 segundos.",
        "Parpadea conscientemente 10 veces.",
        "Cierra los ojos y descansa 30 segundos.",
    ],
    "yawn": [
        "Levántate y camina 2 minutos.",
        "Toma agua o una bebida sin cafeína.",
        "Realiza 5 respiraciones profundas.",
    ],
    "drowsiness": [
        "Tus ojos han estado cerrados una fracción alta del último minuto.",
        "Interrumpe la tarea: levántate y camina 5 minutos.",
        "Ventila el ambiente y toma agua.",
        "Si la somnolencia persiste, evita tareas que requieran atención sostenida.",
    ],
}


class AlertNotifier:
    """
    Componente de alerta modal bloqueante.

    Uso::

        notifier = AlertNotifier(page)
        notifier.show(alert_event)
    """

    def __init__(self, page: ft.Page,
                 on_dismissed: Optional[Callable] = None):
        """
        Parameters
        ----------
        page:
            Instancia de ft.Page de la aplicación Flet.
        on_dismissed:
            Callback opcional llamado cuando el usuario cierra la alerta.
        """
        self._page = page
        self._on_dismissed = on_dismissed
        self._dialog: Optional[ft.AlertDialog] = None
        self._current_event = None
        self._lock = threading.Lock()

    def show(self, alert_event) -> None:
        """
        Muestra el modal de alerta basado en el AlertEvent del FSM.

        Parameters
        ----------
        alert_event:
            Instancia de fusion_fsm.AlertEvent.
        """
        # Dos condiciones pueden confirmarse en el mismo frame (p. ej. postura
        # + fatiga). Sin este guardia, el segundo `show()` sustituia
        # `self._dialog` mientras el primer modal seguia abierto en el overlay:
        # el boton "Entendido" solo cerraba el ultimo, y el anterior quedaba
        # bloqueando la ventana para siempre. Se muestra el primero y el
        # segundo se descarta (ya quedo registrado en la bitacora).
        with self._lock:
            if self._dialog is not None and self._dialog.open:
                logger.info(
                    "Alerta %s omitida en la UI: ya hay un modal abierto "
                    "(el evento si quedo registrado en la bitacora).",
                    alert_event.alert_type.value,
                )
                return

        alert_type = alert_event.alert_type.value
        colors = _ALERT_COLORS.get(alert_type, {
            "bg": "#E74C3C", "icon": "⚠️", "label": "ALERTA"
        })
        instructions = _PAUSE_INSTRUCTIONS.get(alert_type, [
            "Tómate un descanso de 5 minutos."
        ])

        # Encabezado del modal
        header = ft.Container(
            content=ft.Column([
                ft.Text(
                    colors["icon"] + "  " + colors["label"],
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=ft.colors.WHITE,
                ),
                ft.Text(
                    alert_event.message,
                    size=13,
                    color=ft.colors.WHITE70,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            ),
            bgcolor=colors["bg"],
            padding=ft.padding.symmetric(vertical=18, horizontal=24),
            border_radius=ft.border_radius.only(top_left=12, top_right=12),
        )

        # Instrucciones de pausa activa
        instr_items = [
            ft.Row([
                ft.Icon(ft.icons.ARROW_RIGHT, color=ft.colors.BLUE_400, size=16),
                ft.Text(instr, size=13, color=ft.colors.WHITE70, expand=True),
            ])
            for instr in instructions
        ]

        body = ft.Container(
            content=ft.Column([
                ft.Text(
                    "Pausa Activa Recomendada",
                    size=15,
                    weight=ft.FontWeight.W_600,
                    color=ft.colors.WHITE,
                ),
                ft.Divider(color=ft.colors.WHITE24, height=1),
                *instr_items,
                ft.Container(height=8),
                ft.Row([
                    ft.Icon(ft.icons.TIMER_OUTLINED, color=ft.colors.AMBER_400, size=14),
                    ft.Text(
                        f"Duración de la condición: {alert_event.duration_sec:.1f}s",
                        size=12,
                        color=ft.colors.AMBER_400,
                    ),
                ]),
            ],
            spacing=10,
            ),
            bgcolor="#1E1E2E",
            padding=ft.padding.symmetric(vertical=16, horizontal=24),
        )

        def _dismiss(_):
            dialog = self._dialog
            if dialog is not None:
                dialog.open = False
                # Sacarlo del overlay: `page.overlay` es una lista que Flet
                # serializa entera en cada `page.update()`. Dejar ahi un modal
                # cerrado por cada alerta de la sesion hace crecer el arbol de
                # widgets sin limite durante una jornada de trabajo.
                if dialog in self._page.overlay:
                    self._page.overlay.remove(dialog)
                self._dialog = None
            self._page.update()
            if self._on_dismissed:
                self._on_dismissed(alert_event)

        btn_ok = ft.ElevatedButton(
            text="✓  Entendido, voy a pausar",
            on_click=_dismiss,
            style=ft.ButtonStyle(
                bgcolor=colors["bg"],
                color=ft.colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(vertical=12, horizontal=20),
            ),
        )

        self._dialog = ft.AlertDialog(
            modal=True,
            content=ft.Column(
                [header, body],
                spacing=0,
                tight=True,
            ),
            actions=[btn_ok],
            actions_alignment=ft.MainAxisAlignment.CENTER,
            content_padding=ft.padding.all(0),
            bgcolor="#1E1E2E",
            shape=ft.RoundedRectangleBorder(radius=12),
        )

        self._page.overlay.append(self._dialog)
        self._dialog.open = True
        self._current_event = alert_event
        self._page.update()

    @property
    def is_open(self) -> bool:
        """True si hay un modal de alerta visible en este momento."""
        return self._dialog is not None and self._dialog.open
