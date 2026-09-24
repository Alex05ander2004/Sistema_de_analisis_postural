"""
calibration_view.py
-------------------
Tarjeta de calibración guiada del panel lateral.

Muestra las instrucciones, comprueba el encuadre en vivo (rostro, hombros,
distancia), sigue el progreso de la medición y resume el resultado. La lógica
vive en src/fusion/guided_calibration.py; esta clase solo pinta el estado que
el hilo de inferencia publica en cada frame.

Las comprobaciones se muestran una a una, y no como un único "encuadre
incorrecto", porque el usuario necesita saber QUÉ corregir: acercarse, girar
la cara o subir la cámara son acciones distintas.

Autor: Tesis Huisa Perez, UNSA 2026
"""

from typing import Optional

import flet as ft

_OK = "#00C853"
_BAD = "#FF4444"
_PENDING = "#555555"
_ACCENT = "#00BCD4"
_WARN = "#FFAA00"
_CARD = "#16162A"


class _CheckRow:
    """Fila de una comprobación de encuadre: icono, texto y detalle."""

    def __init__(self, label: str):
        self._icon = ft.Icon(ft.icons.RADIO_BUTTON_UNCHECKED,
                             color=_PENDING, size=16)
        self._label = ft.Text(label, size=12, color=ft.colors.WHITE70)
        self._detail = ft.Text("", size=11, color=ft.colors.WHITE38)

    def build(self) -> ft.Row:
        return ft.Row([self._icon, self._label,
                       ft.Container(expand=True), self._detail], spacing=8)

    def set(self, ok: Optional[bool], detail: str = "") -> None:
        if ok is None:
            self._icon.name = ft.icons.RADIO_BUTTON_UNCHECKED
            self._icon.color = _PENDING
        elif ok:
            self._icon.name = ft.icons.CHECK_CIRCLE
            self._icon.color = _OK
        else:
            self._icon.name = ft.icons.CANCEL
            self._icon.color = _BAD
        self._detail.value = detail


class CalibrationView:
    """
    Tarjeta de calibración.

    Uso::

        view = CalibrationView(min_distance_m=0.50, max_distance_m=0.70)
        sidebar.controls.insert(0, view.build())
        view.update(state.calibration, state.mode)   # en el bucle de la UI
    """

    def __init__(self, min_distance_m: float, max_distance_m: float,
                 calibration_sec: float = 20.0):
        self._min_d = min_distance_m
        self._max_d = max_distance_m

        rango = f"{min_distance_m:.2f}–{max_distance_m:.2f} m"
        pasos = [
            f"Siéntate a {rango} de la cámara, con la cámara a la altura "
            f"de tus ojos.",
            "Espalda recta y apoyada, hombros relajados y nivelados.",
            "Mira la pantalla con normalidad, sin entornar los ojos.",
            f"Pulsa «Calibrar» y quédate quieto unos {calibration_sec:.0f} s.",
        ]
        self._steps = ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Text(str(i), size=10, color="#0A0A16",
                                    weight=ft.FontWeight.BOLD),
                    bgcolor=_ACCENT, width=18, height=18, border_radius=9,
                    alignment=ft.alignment.center,
                ),
                ft.Text(texto, size=12, color=ft.colors.WHITE70, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
            for i, texto in enumerate(pasos, 1)
        ], spacing=6)

        self._chk_face = _CheckRow("Rostro detectado")
        self._chk_body = _CheckRow("Hombros visibles")
        self._chk_dist = _CheckRow(f"Distancia {rango}")

        self._phase_text = ft.Text("Aún no has calibrado.", size=13,
                                   weight=ft.FontWeight.W_600,
                                   color=ft.colors.WHITE)
        self._progress = ft.ProgressBar(value=0, color=_ACCENT,
                                        bgcolor="#2A2A3A", height=8,
                                        border_radius=4, visible=False)
        self._message = ft.Text("", size=12, color=ft.colors.WHITE54)
        self._result = ft.Column([], spacing=4, visible=False)

        self._card: Optional[ft.Container] = None

    # ------------------------------------------------------------------

    def build(self) -> ft.Container:
        self._card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.icons.TUNE, color=_ACCENT, size=18),
                    ft.Text("CALIBRACIÓN", size=13, weight=ft.FontWeight.BOLD,
                            color=ft.colors.WHITE),
                ], spacing=8),
                ft.Text("El sistema mide TU postura neutra y TU ojo abierto, "
                        "y después vigila las desviaciones respecto a ellos.",
                        size=11, color=ft.colors.WHITE54),
                ft.Divider(color=ft.colors.WHITE12, height=1),
                self._steps,
                ft.Divider(color=ft.colors.WHITE12, height=1),
                ft.Text("Comprobación del encuadre", size=11,
                        color=ft.colors.WHITE54),
                self._chk_face.build(),
                self._chk_body.build(),
                self._chk_dist.build(),
                ft.Divider(color=ft.colors.WHITE12, height=1),
                self._phase_text,
                self._progress,
                self._message,
                self._result,
            ], spacing=8),
            bgcolor=_CARD,
            border=ft.border.all(1, "#2A2A4A"),
            border_radius=10,
            padding=ft.padding.all(14),
        )
        return self._card

    # ------------------------------------------------------------------

    def update(self, calibration: dict, mode: str) -> None:
        """Pinta el estado publicado por el hilo de inferencia."""
        if self._card is None:
            return
        # Durante el seguimiento la tarjeta se oculta: el panel de telemetría
        # es lo que el usuario necesita ver entonces.
        self._card.visible = mode != "monitoring"
        if not calibration:
            return

        checks = calibration.get("checks", {})
        self._chk_face.set(checks.get("face"))
        self._chk_body.set(checks.get("body"))
        d = checks.get("distance_m")
        detalle = f"{d:.2f} m" if d is not None else "—"
        self._chk_dist.set(checks.get("distance_ok") if d is not None else None,
                           detalle)

        phase = calibration.get("phase", "idle")
        msg = calibration.get("message", "")
        self._progress.visible = phase in ("countdown", "collecting")
        self._result.visible = False
        self._message.color = ft.colors.WHITE54

        if phase == "countdown":
            restante = calibration.get("countdown_remaining", 0.0)
            self._phase_text.value = f"Prepárate… {max(1, round(restante))}"
            self._phase_text.color = _WARN
            self._progress.value = None          # barra indeterminada
            self._message.value = msg
        elif phase == "collecting":
            prog = calibration.get("progress", 0.0)
            self._phase_text.value = f"Midiendo… {prog * 100:.0f}%"
            self._phase_text.color = _ACCENT
            self._progress.value = prog
            self._message.value = msg
        elif phase == "done":
            self._phase_text.value = "✓ Calibración completada"
            self._phase_text.color = _OK
            self._message.value = ("Pulsa «Iniciar seguimiento» cuando quieras."
                                   if mode == "ready" else "")
            self._show_result(calibration.get("result") or {})
        elif phase == "failed":
            self._phase_text.value = "✗ No se pudo calibrar"
            self._phase_text.color = _BAD
            self._message.value = msg
            self._message.color = _BAD
        else:
            ok = checks.get("ok")
            self._phase_text.value = ("Listo para calibrar" if ok
                                      else "Ajusta el encuadre para calibrar")
            self._phase_text.color = _OK if ok else ft.colors.WHITE
            self._message.value = msg

    def _show_result(self, result: dict) -> None:
        filas = []
        if result.get("cervical_baseline") is not None:
            filas.append(("Postura neutra (θc)",
                          f"{result['cervical_baseline']:.1f}°"))
        if result.get("ear_threshold") is not None:
            filas.append(("Umbral de ojo cerrado",
                          f"{result['ear_threshold']:.3f}"))
        if result.get("distance_m") is not None:
            filas.append(("Distancia media", f"{result['distance_m']:.2f} m"))
        self._result.controls = [
            ft.Row([ft.Text(k, size=11, color=ft.colors.WHITE54),
                    ft.Container(expand=True),
                    ft.Text(v, size=12, color=ft.colors.WHITE,
                            weight=ft.FontWeight.W_600)])
            for k, v in filas
        ]
        self._result.visible = bool(filas)
