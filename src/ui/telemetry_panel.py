"""
telemetry_panel.py
------------------
Panel de telemetría en tiempo real con semaforización verde/amarillo/rojo.

Muestra:
  - Ángulo cervical θc (con barra de progreso y color semáforo)
  - Asimetría escapular ΔE
  - EAR promedio (fatiga ocular)
  - Apertura bucal
  - FPS actual del sistema
  - Estado de detección (pose / cara)
  - Progreso de temporizadores del FSM

Diseño: dark mode, estilo dashboard, actualización cada 100 ms.

Autor: Generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import flet as ft
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers de semaforización
# ---------------------------------------------------------------------------

def _traffic_color(value: float, warn_threshold: float,
                   alert_threshold: float,
                   invert: bool = False) -> str:
    """
    Retorna color hex según posición del valor respecto a umbrales.

    invert=True → valores bajos son problemáticos (ej. EAR)
    """
    if invert:
        if value <= alert_threshold:
            return "#FF4444"   # rojo
        elif value <= warn_threshold:
            return "#FFAA00"   # amarillo
        else:
            return "#00C853"   # verde
    else:
        if value >= alert_threshold:
            return "#FF4444"
        elif value >= warn_threshold:
            return "#FFAA00"
        else:
            return "#00C853"


def _status_dot(color: str) -> ft.Container:
    """Circulito indicador de estado."""
    return ft.Container(
        width=10, height=10,
        bgcolor=color,
        border_radius=5,
    )


# ---------------------------------------------------------------------------
# Componente de una fila de métrica
# ---------------------------------------------------------------------------

class MetricRow:
    """
    Fila de una métrica individual: etiqueta, valor numérico,
    barra de progreso y color semáforo.
    """

    def __init__(self, label: str, unit: str = "°",
                 max_val: float = 45.0,
                 warn_threshold: float = 10.0,
                 alert_threshold: float = 15.0,
                 invert: bool = False,
                 decimals: int = 1):
        """
        decimals:
            Cifras decimales del valor mostrado. El EAR y el MAR se mueven en
            centesimas alrededor de umbrales como 0.21: con 1 decimal (el
            valor fijo de la version anterior) el panel mostraba "0.2" tanto
            para 0.15 como para 0.24, y era imposible leer en pantalla si la
            metrica estaba cerca del umbral.
        """
        self.label = label
        self.unit = unit
        self.max_val = max_val
        self.warn_threshold = warn_threshold
        self.alert_threshold = alert_threshold
        self.invert = invert
        self.decimals = decimals

        self._dot = _status_dot("#555555")
        self._value_text = ft.Text(
            "—", size=22, weight=ft.FontWeight.BOLD,
            color=ft.colors.WHITE,
        )
        self._progress = ft.ProgressBar(
            value=0,
            color="#00C853",
            bgcolor="#2A2A3A",
            height=6,
            border_radius=3,
        )
        self._label_text = ft.Text(
            label, size=11, color=ft.colors.WHITE54,
            weight=ft.FontWeight.W_500,
        )

    def build(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    self._dot,
                    self._label_text,
                    ft.Container(expand=True),
                    self._value_text,
                    ft.Text(self.unit, size=11, color=ft.colors.WHITE38),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self._progress,
            ], spacing=4),
            bgcolor="#16162A",
            border_radius=8,
            padding=ft.padding.symmetric(vertical=10, horizontal=14),
        )

    def update(self, value: float) -> None:
        """Actualiza la fila con el nuevo valor."""
        color = _traffic_color(
            value,
            self.warn_threshold,
            self.alert_threshold,
            self.invert,
        )
        self._dot.bgcolor = color
        self._value_text.value = f"{value:.{self.decimals}f}"
        self._value_text.color = color
        progress = min(abs(value) / self.max_val, 1.0)
        self._progress.value = progress
        self._progress.color = color


# ---------------------------------------------------------------------------
# Panel de temporizadores del FSM
# ---------------------------------------------------------------------------

class FsmTimerPanel:
    """Mini panel que muestra el progreso de los temporizadores del FSM."""

    def __init__(self):
        self._cervical_bar = ft.ProgressBar(
            value=0, color="#FF4444", bgcolor="#2A2A3A", height=5, border_radius=3
        )
        self._ear_bar = ft.ProgressBar(
            value=0, color="#9B59B6", bgcolor="#2A2A3A", height=5, border_radius=3
        )
        self._shoulder_bar = ft.ProgressBar(
            value=0, color="#FF8800", bgcolor="#2A2A3A", height=5, border_radius=3
        )
        self._cervical_label = ft.Text("Cervical: 0.0s", size=10, color=ft.colors.WHITE54)
        self._shoulder_label = ft.Text("Hombros: 0.0s", size=10, color=ft.colors.WHITE54)
        self._ear_label = ft.Text("Fatiga: 0.0s", size=10, color=ft.colors.WHITE54)

    def build(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Text("⏱ Temporizadores FSM", size=11,
                        weight=ft.FontWeight.W_600, color=ft.colors.WHITE54),
                ft.Column([
                    ft.Row([self._cervical_label, ft.Container(expand=True)]),
                    self._cervical_bar,
                    ft.Container(height=4),
                    ft.Row([self._shoulder_label, ft.Container(expand=True)]),
                    self._shoulder_bar,
                    ft.Container(height=4),
                    ft.Row([self._ear_label, ft.Container(expand=True)]),
                    self._ear_bar,
                ], spacing=2),
            ], spacing=6),
            bgcolor="#16162A",
            border_radius=8,
            padding=ft.padding.symmetric(vertical=10, horizontal=14),
        )

    def update(self, timer_status: dict) -> None:
        cerv_elapsed = timer_status.get("cervical_elapsed_sec", 0)
        cerv_window  = timer_status.get("cervical_window_sec", 5)
        shou_elapsed = timer_status.get("shoulder_elapsed_sec", 0)
        shou_window  = timer_status.get("shoulder_window_sec", 5)
        ear_elapsed  = timer_status.get("ear_elapsed_sec", 0)
        ear_window   = timer_status.get("ear_window_sec", 3)

        self._cervical_bar.value = min(cerv_elapsed / max(cerv_window, 0.1), 1.0)
        self._shoulder_bar.value = min(shou_elapsed / max(shou_window, 0.1), 1.0)
        self._ear_bar.value = min(ear_elapsed / max(ear_window, 0.1), 1.0)
        self._cervical_label.value = f"Cervical: {cerv_elapsed:.1f}s / {cerv_window:.0f}s"
        self._shoulder_label.value = f"Hombros: {shou_elapsed:.1f}s / {shou_window:.0f}s"
        self._ear_label.value = f"Fatiga: {ear_elapsed:.1f}s / {ear_window:.0f}s"


# ---------------------------------------------------------------------------
# Panel de telemetría completo
# ---------------------------------------------------------------------------

class TelemetryPanel:
    """
    Panel de telemetría completo con semaforización.

    Uso::

        panel = TelemetryPanel(thresholds)
        page.add(panel.build())

        # En el loop de actualización:
        panel.update(metrics, fps=29.8, timer_status={...})
        page.update()
    """

    def __init__(self, thresholds: dict):
        post = thresholds.get("postural", {})
        fat  = thresholds.get("fatigue", {})
        cam  = thresholds.get("camera", {})

        cervical_max = post.get("cervical_angle_max_deg", 30.0)
        shoulder_max = post.get("shoulder_asymmetry_max_deg", 10.0)
        ear_thr      = fat.get("ear_threshold", 0.21)
        mar_thr      = fat.get("mouth_opening_threshold", 0.45)
        self._min_dist = cam.get("min_distance_m", 0.50)
        self._max_dist = cam.get("max_distance_m", 0.70)

        # Filas de métricas
        self._row_cervical = MetricRow(
            "Ángulo Cervical (θc)", unit="°",
            max_val=45.0,
            warn_threshold=cervical_max * 0.7,
            alert_threshold=cervical_max,
        )
        self._row_shoulder = MetricRow(
            "Asimetría Escapular (ΔE)", unit="°",
            max_val=30.0,
            warn_threshold=shoulder_max * 0.7,
            alert_threshold=shoulder_max,
        )
        self._row_ear = MetricRow(
            "EAR Promedio", unit="",
            max_val=0.5,
            warn_threshold=ear_thr + 0.05,
            alert_threshold=ear_thr,
            invert=True,
            decimals=3,
        )
        self._row_mouth = MetricRow(
            "Apertura Bucal (MAR)", unit="",
            max_val=1.0,
            # Los umbrales del MAR estaban escritos a mano (0.35 / 0.45) y no
            # seguian a config/thresholds.json: cambiar el umbral en el JSON
            # movia el disparo del FSM pero no el color del panel, de modo que
            # el semaforo podia estar en verde con la alerta sonando.
            warn_threshold=mar_thr * 0.78,
            alert_threshold=mar_thr,
            decimals=3,
        )

        self._fsm_timer_panel = FsmTimerPanel()

        # Indicadores de FPS y detección
        self._fps_text  = ft.Text("—", size=20, weight=ft.FontWeight.BOLD, color="#00BCD4")
        self._pose_icon = ft.Text("🦴 No detectado", size=11, color=ft.colors.WHITE38)
        self._face_icon = ft.Text("👤 No detectado", size=11, color=ft.colors.WHITE38)

        # Distancia estimada: el rango 0.50–0.70 m validado por el experto solo
        # sirve si el usuario puede comprobar en pantalla que esta dentro de el.
        self._dist_text = ft.Text("— m", size=13, weight=ft.FontWeight.BOLD,
                                  color=ft.colors.WHITE)
        self._dist_hint = ft.Text(
            f"rango {self._min_dist:.2f}–{self._max_dist:.2f} m",
            size=10, color=ft.colors.WHITE38)

        # Indicadores de fatiga derivados (no disparan alerta por si solos)
        self._perclos_text = ft.Text("—", size=13, weight=ft.FontWeight.BOLD,
                                     color=ft.colors.WHITE)
        self._blink_text = ft.Text("—", size=13, weight=ft.FontWeight.BOLD,
                                   color=ft.colors.WHITE)

    def build(self) -> ft.Column:
        """Construye y devuelve el widget del panel."""
        return ft.Column(
            [
                # Encabezado
                ft.Container(
                    content=ft.Row([
                        ft.Text(
                            "📊  TELEMETRÍA EN TIEMPO REAL",
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color=ft.colors.WHITE,
                        ),
                        ft.Container(expand=True),
                        ft.Column([
                            ft.Text("FPS", size=9, color=ft.colors.WHITE38),
                            self._fps_text,
                        ], horizontal_alignment=ft.CrossAxisAlignment.END, spacing=0),
                    ]),
                    bgcolor="#0D0D1A",
                    padding=ft.padding.symmetric(vertical=10, horizontal=14),
                    border_radius=8,
                ),

                # Métricas posturales
                ft.Text("  Postura Corporal", size=11, color=ft.colors.WHITE38,
                        weight=ft.FontWeight.W_500),
                self._row_cervical.build(),
                self._row_shoulder.build(),

                # Métricas de fatiga
                ft.Text("  Fatiga", size=11, color=ft.colors.WHITE38,
                        weight=ft.FontWeight.W_500),
                self._row_ear.build(),
                self._row_mouth.build(),

                # Indicadores derivados de fatiga
                ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text("PERCLOS (60 s)", size=9, color=ft.colors.WHITE38),
                            self._perclos_text,
                        ], spacing=0),
                        ft.Container(expand=True),
                        ft.Column([
                            ft.Text("Parpadeos/min", size=9, color=ft.colors.WHITE38),
                            self._blink_text,
                        ], horizontal_alignment=ft.CrossAxisAlignment.END, spacing=0),
                    ]),
                    bgcolor="#16162A",
                    border_radius=8,
                    padding=ft.padding.symmetric(vertical=8, horizontal=14),
                ),

                # Temporizadores FSM
                self._fsm_timer_panel.build(),

                # Encuadre / distancia
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.icons.STRAIGHTEN, color=ft.colors.WHITE38, size=14),
                        ft.Text("Distancia", size=11, color=ft.colors.WHITE54),
                        ft.Container(expand=True),
                        self._dist_text,
                        self._dist_hint,
                    ], spacing=6),
                    bgcolor="#16162A",
                    border_radius=8,
                    padding=ft.padding.symmetric(vertical=8, horizontal=14),
                ),

                # Estado de detección
                ft.Container(
                    content=ft.Row([
                        self._pose_icon,
                        ft.Container(expand=True),
                        self._face_icon,
                    ]),
                    bgcolor="#16162A",
                    border_radius=8,
                    padding=ft.padding.symmetric(vertical=8, horizontal=14),
                ),
            ],
            spacing=6,
        )

    def update(self, metrics, fps: float = 0.0,
               timer_status: Optional[dict] = None) -> None:
        """
        Actualiza todos los indicadores del panel.

        Parameters
        ----------
        metrics:
            Instancia de fusion_fsm.SensorMetrics.
        fps:
            FPS actual del sistema.
        timer_status:
            Diccionario de FusionFSM.get_timers_status().
        """
        self._row_cervical.update(metrics.cervical_angle)
        self._row_shoulder.update(abs(metrics.shoulder_asymmetry))
        self._row_ear.update(metrics.ear_avg)
        self._row_mouth.update(metrics.mouth_opening)

        self._fps_text.value = f"{fps:.0f}"
        self._fps_text.color = "#00C853" if fps >= 28 else "#FFAA00" if fps >= 20 else "#FF4444"

        # Estado de detección
        self._pose_icon.value = (
            "🦴 Pose OK" if metrics.pose_detected else "🦴 Sin pose"
        )
        self._pose_icon.color = "#00C853" if metrics.pose_detected else "#FF4444"

        self._face_icon.value = (
            "👤 Cara OK" if metrics.face_detected else "👤 Sin cara"
        )
        self._face_icon.color = "#00C853" if metrics.face_detected else "#FF4444"

        # Distancia estimada cámara-usuario
        d = getattr(metrics, "distance_m", None)
        if d is None:
            self._dist_text.value = "— m"
            self._dist_text.color = ft.colors.WHITE38
        else:
            self._dist_text.value = f"{d:.2f} m"
            self._dist_text.color = (
                "#00C853" if self._min_dist <= d <= self._max_dist else "#FFAA00"
            )

        # PERCLOS y tasa de parpadeo
        perclos = getattr(metrics, "perclos", 0.0) or 0.0
        blink = getattr(metrics, "blink_rate_per_min", 0.0) or 0.0
        self._perclos_text.value = f"{perclos * 100:.0f}%"
        self._perclos_text.color = "#FF4444" if perclos >= 0.15 else ft.colors.WHITE
        self._blink_text.value = f"{blink:.0f}"

        if timer_status:
            self._fsm_timer_panel.update(timer_status)
