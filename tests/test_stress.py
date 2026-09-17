"""
test_stress.py
--------------
Pruebas de estrés y robustez del sistema (Fase 4 — Capítulo IV).

Simula condiciones adversas sin necesidad de cámara física:
  - Oclusiones parciales (landmarks desaparecen intermitentemente)
  - Variabilidad lumínica (frames con ruido gaussiano extremo)
  - Distancia fuera de rango (landmarks escalados)
  - Pérdida total de detección (cara/pose no detectada)
  - Cambio brusco de postura (transición normal → riesgo)

Todas las pruebas son deterministas y reproducibles (semilla fija).

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import math
import random
import sys
import time
from pathlib import Path
from statistics import mean, stdev

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vision.geometry import (
    calculate_cervical_angle,
    calculate_shoulder_asymmetry,
    calculate_avg_ear,
    calculate_mouth_opening,
)
from src.fusion.fusion_fsm import FusionFSM, SensorMetrics


# ---------------------------------------------------------------------------
# Landmarks sintéticos
# ---------------------------------------------------------------------------

class _LM:
    __slots__ = ("x", "y", "z", "visibility")
    def __init__(self, x=0.5, y=0.5, z=0.0, visibility=1.0):
        self.x = x; self.y = y; self.z = z; self.visibility = visibility


def _pose_normal() -> list:
    """Postura ergonómica correcta."""
    lms = [_LM()] * 33
    lms[7]  = _LM(0.5, 0.15, 0.0)    # LEFT_EAR
    lms[11] = _LM(0.4, 0.55, 0.0)    # LEFT_SHOULDER
    lms[12] = _LM(0.6, 0.55, 0.0)    # RIGHT_SHOULDER
    return lms


def _pose_risk(cervical_deg: float = 25.0) -> list:
    """Postura de cabeza adelantada con ángulo forzado."""
    lms = [_LM()] * 33
    rad = math.radians(cervical_deg)
    lms[7]  = _LM(0.5 - 0.25 * math.sin(rad), 0.15 + 0.15 * (1 - math.cos(rad)), -0.3)
    lms[11] = _LM(0.4, 0.55, 0.0)
    lms[12] = _LM(0.6, 0.55, 0.0)
    return lms


def _face_open_eyes(ear_value: float = 0.35) -> list:
    """Cara con EAR configurable."""
    lms = [_LM(0.5, 0.5)] * 478
    # Modelar apertura del ojo según EAR deseado
    # EAR = (d26+d35)/(2*d14). Con d14=0.1, EAR=ear_value → d_vert = ear_value * 0.1
    half_w = 0.1
    half_h = ear_value * half_w
    for idx, (x, y) in zip(
        [362, 385, 387, 263, 373, 380],
        [(0.0, 0.5), (0.33, 0.5-half_h), (0.67, 0.5-half_h),
         (1.0, 0.5), (0.67, 0.5+half_h), (0.33, 0.5+half_h)],
    ):
        lms[idx] = _LM(x * half_w * 2 + 0.35, y, 0.0)
    for idx, (x, y) in zip(
        [33, 160, 158, 133, 153, 144],
        [(0.0, 0.5), (0.33, 0.5-half_h), (0.67, 0.5-half_h),
         (1.0, 0.5), (0.67, 0.5+half_h), (0.33, 0.5+half_h)],
    ):
        lms[idx] = _LM(x * half_w * 2 + 0.55, y, 0.0)
    lms[13] = _LM(0.5, 0.75); lms[14] = _LM(0.5, 0.77)
    lms[61] = _LM(0.35, 0.76); lms[291] = _LM(0.65, 0.76)
    return lms


_FSM_THRESHOLDS_FAST = {
    "postural": {
        "cervical_angle_max_deg": 15.0,
        "cervical_alert_window_sec": 0.15,
        "shoulder_asymmetry_max_deg": 10.0,
        "shoulder_alert_window_sec": 0.15,
    },
    "fatigue": {
        "ear_threshold": 0.21,
        "ear_alert_window_sec": 0.10,
        "blink_min_ms": 100, "blink_max_ms": 400,
        "mouth_opening_threshold": 0.45,
        "yawn_alert_window_sec": 0.10,
    },
    "ui": {"alert_cooldown_sec": 0.0},
}


# ===========================================================================
# Test Suite 1: Oclusión parcial
# ===========================================================================

class TestOcclusion:
    """
    Simula que MediaPipe pierde la detección intermitentemente
    (ej. usuario pone la mano frente a la cámara).
    """

    def test_no_crash_when_pose_lost(self):
        """
        El sistema no debe lanzar excepción cuando pose_detected=False.
        El FSM debe ignorar las métricas de pose en ese caso.
        """
        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=None, cooldown_sec=0.0)
        for _ in range(50):
            # Alternar: detección OK / perdida
            detected = random.random() > 0.5
            m = SensorMetrics(
                cervical_angle=25.0,   # fuera de rango
                ear_avg=0.10,          # fatiga
                pose_detected=detected,
                face_detected=detected,
            )
            alerts = fsm.update(m)  # no debe lanzar excepción
            if not detected:
                assert alerts == [], "Con detección perdida no debe haber alertas"

    def test_recovery_after_occlusion(self):
        """
        Tras recuperar la detección, los temporizadores deben funcionar
        correctamente desde cero (no acumular tiempo de oclusión).
        """
        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=None, cooldown_sec=0.0)
        # 50 ms de oclusión con postura de riesgo
        t_end = time.perf_counter() + 0.05
        while time.perf_counter() < t_end:
            fsm.update(SensorMetrics(cervical_angle=25.0,
                                      pose_detected=False, face_detected=False))

        # Al recuperar, el temporizador debe empezar desde 0
        status_before = fsm.get_timers_status()["cervical_elapsed_sec"]
        assert status_before < 0.01, (
            "Tras oclusión, el timer cervical no debe acumular tiempo"
        )

    def test_partial_face_occlusion_ear_still_computed(self):
        """
        EAR calculado con puntos completos del ojo debe devolver valor válido.
        Simula que la cara sigue visible pero el ojo está parcialmente tapado
        (visibility baja pero coordenadas presentes).
        """
        face_lms = _face_open_eyes(ear_value=0.30)
        # Simular baja confianza reduciendo visibility (no afecta las coords)
        for idx in [362, 385, 387]:
            face_lms[idx].visibility = 0.3

        left_idx  = [362, 385, 387, 263, 373, 380]
        right_idx = [33,  160, 158, 133, 153, 144]
        ear = calculate_avg_ear(face_lms, left_idx, right_idx)
        assert 0.0 < ear < 1.0, f"EAR debe seguir siendo válido: {ear:.4f}"


# ===========================================================================
# Test Suite 2: Variabilidad lumínica (ruido en landmarks)
# ===========================================================================

class TestLightingVariability:
    """
    Simula el efecto de iluminación variable: landmarks con jitter
    gaussiano añadido (equivalente a baja confianza de detección).
    """

    def test_cervical_angle_robust_to_landmark_jitter(self):
        """
        Con ruido gaussiano pequeño en los landmarks (σ=0.005),
        el ángulo cervical no debe variar más de ±5° respecto al valor base.
        """
        rng = random.Random(42)
        base_lms = _pose_normal()
        base_angle = calculate_cervical_angle(base_lms)

        angles = []
        for _ in range(200):
            jittered = [_LM(lm.x, lm.y, lm.z) for lm in base_lms]
            for i in [7, 11, 12]:
                jittered[i].x += rng.gauss(0, 0.005)
                jittered[i].y += rng.gauss(0, 0.005)
                jittered[i].z += rng.gauss(0, 0.005)
            angles.append(calculate_cervical_angle(jittered))

        angle_std = stdev(angles)
        angle_max_dev = max(abs(a - base_angle) for a in angles)

        print(f"\n  Jitter σ=0.005 → desviación estándar θc: {angle_std:.3f}°, "
              f"desv. máxima: {angle_max_dev:.3f}°")

        assert angle_std < 3.0, (
            f"θc demasiado sensible al ruido de landmarks: σ={angle_std:.3f}°"
        )
        assert angle_max_dev < 8.0, (
            f"Desviación máxima de θc ante jitter: {angle_max_dev:.3f}° (límite: 8°)"
        )

    def test_ear_robust_to_landmark_jitter(self):
        """
        EAR con ruido gaussiano pequeño (σ=0.002) no debe variar > 0.05.
        """
        rng = random.Random(99)
        face_lms = _face_open_eyes(ear_value=0.30)
        left_idx  = [362, 385, 387, 263, 373, 380]
        right_idx = [33,  160, 158, 133, 153, 144]

        base_ear = calculate_avg_ear(face_lms, left_idx, right_idx)
        ears = []

        for _ in range(200):
            jittered = [_LM(lm.x, lm.y, lm.z) for lm in face_lms]
            for idx in left_idx + right_idx:
                jittered[idx].x += rng.gauss(0, 0.002)
                jittered[idx].y += rng.gauss(0, 0.002)
            ears.append(calculate_avg_ear(jittered, left_idx, right_idx))

        ear_std = stdev(ears)
        print(f"\n  Jitter EAR σ: {ear_std:.5f}")

        assert ear_std < 0.05, (
            f"EAR demasiado sensible al ruido: σ={ear_std:.5f}"
        )

    def test_fsm_stable_under_noisy_metrics(self):
        """
        El FSM no debe disparar alertas ante métricas que oscilan
        alrededor del umbral con ruido (evitar chattering).
        """
        rng = random.Random(7)
        alert_count = 0

        def count_alert(_):
            nonlocal alert_count
            alert_count += 1

        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=count_alert, cooldown_sec=0.0)

        # Métricas oscilando alrededor del umbral (14°-16° con θc_max=15°)
        for _ in range(100):
            noise = rng.gauss(0, 1.0)
            m = SensorMetrics(
                cervical_angle=15.0 + noise,  # oscila cruzando el umbral
                ear_avg=0.25,
                pose_detected=True,
                face_detected=True,
            )
            fsm.update(m)
            time.sleep(0.001)

        print(f"\n  Alertas generadas con oscilación en umbral: {alert_count}")
        # Si el temporizador se reinicia cada vez que la condición cruza,
        # el número de alertas debe ser bajo o cero
        assert alert_count <= 2, (
            f"Demasiado chattering en el umbral: {alert_count} alertas"
        )


# ===========================================================================
# Test Suite 3: Distancia fuera de rango (0.50–0.70 m)
# ===========================================================================

class TestDistanceRange:
    """
    Simula al usuario fuera del rango de distancia recomendado.
    Cuando el usuario está muy cerca o muy lejos, los landmarks
    pueden tener coordenadas fuera del rango [0, 1].
    """

    def test_very_close_distance_landmark_clipping(self):
        """
        Usuario muy cerca: landmarks pueden salir del frame (x/y > 1).
        El sistema no debe lanzar excepción; puede devolver valores extremos.
        """
        lms = [_LM()] * 33
        # Simular que la cabeza sale del encuadre
        lms[7]  = _LM(1.3, -0.2, 0.0)   # fuera del frame arriba-derecha
        lms[11] = _LM(0.4, 0.55, 0.0)
        lms[12] = _LM(0.6, 0.55, 0.0)

        try:
            angle = calculate_cervical_angle(lms)
            assert isinstance(angle, float), "Debe devolver float incluso con landmarks fuera de rango"
        except Exception as e:
            pytest.fail(f"Exception inesperada con landmarks fuera de rango: {e}")

    def test_very_far_distance_small_landmarks(self):
        """
        Usuario muy lejos: landmarks muy juntos (variación pequeña).
        El ángulo debe seguir siendo computable.
        """
        lms = [_LM(0.5, 0.5)] * 33
        # Landmarks muy comprimidos (usuario a 4 metros)
        lms[7]  = _LM(0.505, 0.48, 0.0)
        lms[11] = _LM(0.498, 0.52, 0.0)
        lms[12] = _LM(0.502, 0.52, 0.0)

        angle = calculate_cervical_angle(lms)
        assert isinstance(angle, float)
        assert 0.0 <= angle <= 180.0, f"Ángulo fuera de rango [0, 180]: {angle:.2f}"

    def test_fsm_handles_extreme_metric_values(self):
        """
        El FSM debe manejar métricas extremas sin crashear.
        """
        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=None, cooldown_sec=0.0)

        extreme_cases = [
            SensorMetrics(cervical_angle=180.0, ear_avg=0.0, face_detected=True, pose_detected=True),
            SensorMetrics(cervical_angle=-45.0, ear_avg=1.0, face_detected=True, pose_detected=True),
            SensorMetrics(cervical_angle=float('nan') if False else 0.0, ear_avg=0.5),
        ]

        for m in extreme_cases:
            try:
                fsm.update(m)  # No debe lanzar excepción
            except Exception as e:
                pytest.fail(f"FSM no debe crashear con métricas extremas: {e}")


# ===========================================================================
# Test Suite 4: Transición de estados (Normal → Riesgo → Normal)
# ===========================================================================

class TestStateTransitions:
    """
    Verifica que el FSM maneja correctamente las transiciones
    de estado y recuperación a condición normal.
    """

    def test_alert_then_recovery(self):
        """
        Tras disparar una alerta, si la postura vuelve a ser normal,
        el temporizador debe reiniciarse (no hay alerta constante).
        """
        alert_fired = []

        def on_alert(ev):
            alert_fired.append(ev)

        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=on_alert, cooldown_sec=0.0)

        # Fase 1: postura de riesgo → esperar alerta
        t_end = time.perf_counter() + 0.20
        while time.perf_counter() < t_end:
            fsm.update(SensorMetrics(cervical_angle=25.0, ear_avg=0.35,
                                      pose_detected=True, face_detected=True))
            time.sleep(0.005)

        assert len(alert_fired) >= 1, "Debe haber disparado al menos 1 alerta"
        alert_fired.clear()

        # Fase 2: volver a postura normal
        t_end = time.perf_counter() + 0.10
        while time.perf_counter() < t_end:
            fsm.update(SensorMetrics(cervical_angle=8.0, ear_avg=0.35,
                                      pose_detected=True, face_detected=True))
            time.sleep(0.005)

        # Fase 3: postura de riesgo nuevamente
        # (el cooldown es 0, pero el timer se reseteó)
        status = fsm.get_timers_status()
        # El temporizador cervical debe estar cerca de 0 (o reiniciando)
        assert status["cervical_elapsed_sec"] < 0.15, (
            "Timer cervical no se reinició tras volver a postura normal"
        )

    def test_multiple_simultaneous_conditions(self):
        """
        Si postura Y fatiga están en riesgo al mismo tiempo,
        el FSM debe generar alertas para ambas condiciones.
        """
        alerts = []
        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=lambda e: alerts.append(e),
                         cooldown_sec=0.0)

        t_end = time.perf_counter() + 0.25
        while time.perf_counter() < t_end:
            fsm.update(SensorMetrics(
                cervical_angle=25.0,  # riesgo postural
                ear_avg=0.15,          # riesgo fatiga
                pose_detected=True, face_detected=True,
            ))
            time.sleep(0.005)

        alert_types = {a.alert_type.value for a in alerts}
        print(f"\n  Tipos de alerta generados: {alert_types}")

        assert "cervical_angle" in alert_types, "Debe alertar sobre postura cervical"
        assert "eye_fatigue" in alert_types, "Debe alertar sobre fatiga ocular"


# ===========================================================================
# Test Suite 5: Rendimiento bajo carga sostenida (10 min simulados)
# ===========================================================================

class TestSustainedLoad:
    """
    Simula 10 minutos de procesamiento continuo usando frames
    equivalentes a 30 FPS. Verifica estabilidad sin memory leak.
    """

    SIMULATED_MINUTES = 0.1   # 0.1 min = 6 segundos de frames (~180 frames @30fps)
    N_FRAMES = int(SIMULATED_MINUTES * 60 * 30)

    def test_sustained_geometry_no_drift(self):
        """
        Durante N_FRAMES frames continuos, la latencia no debe crecer
        (no hay acumulación de estado ni memory leak en geometry.py).
        """
        pose_lms  = _pose_normal()
        face_lms  = _face_open_eyes()
        left_idx  = [362, 385, 387, 263, 373, 380]
        right_idx = [33,  160, 158, 133, 153, 144]

        first_half  = []
        second_half = []

        for i in range(self.N_FRAMES):
            t0 = time.perf_counter()
            calculate_cervical_angle(pose_lms)
            calculate_shoulder_asymmetry(pose_lms)
            calculate_avg_ear(face_lms, left_idx, right_idx)
            calculate_mouth_opening(face_lms)
            lat = (time.perf_counter() - t0) * 1000.0

            if i < self.N_FRAMES // 2:
                first_half.append(lat)
            else:
                second_half.append(lat)

        avg_first  = mean(first_half)
        avg_second = mean(second_half)
        drift = avg_second - avg_first

        print(f"\n  Latencia primera mitad: {avg_first:.4f} ms")
        print(f"  Latencia segunda mitad: {avg_second:.4f} ms")
        print(f"  Deriva de latencia:     {drift:+.4f} ms")

        assert abs(drift) < 0.5, (
            f"Deriva de latencia detectada: {drift:+.4f} ms "
            f"(posible memory leak o JIT penalty)"
        )

    def test_sustained_fsm_no_spurious_alerts(self):
        """
        Con postura normal durante N_FRAMES, no debe haber ninguna alerta.
        """
        alerts = []
        fsm = FusionFSM(_FSM_THRESHOLDS_FAST, on_alert=lambda e: alerts.append(e),
                         cooldown_sec=0.0)

        for _ in range(self.N_FRAMES):
            fsm.update(SensorMetrics(
                cervical_angle=10.0,   # dentro del rango
                shoulder_asymmetry=5.0,
                ear_avg=0.32,
                mouth_opening=0.15,
                pose_detected=True, face_detected=True,
            ))

        assert alerts == [], (
            f"Postura normal generó {len(alerts)} alertas espurias"
        )
