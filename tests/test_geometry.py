"""
Tests unitarios para geometry.py — Capítulo III.3.3.2.C.

Los landmarks de prueba se construyen a partir de un **modelo antropométrico
en píxeles** (`_make_pose`), no con coordenadas normalizadas escritas a mano.
La versión anterior de estos tests colocaba la oreja izquierda exactamente
sobre el punto medio de los hombros (x = 0.5), una postura anatómicamente
imposible — la oreja está ~7 cm fuera del plano medio sagital — y por eso
daban θc ≈ 0° mientras el sistema real medía 41° de sesgo en postura neutra.
Partir de centímetros y convertir a píxeles hace que un test que pasa
signifique algo sobre una persona real.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vision.geometry import (
    DEFAULT_FRAME_SIZE,
    calculate_cervical_angle,
    calculate_cervical_components,
    calculate_shoulder_asymmetry,
    calculate_ear,
    calculate_avg_ear,
    calculate_mouth_opening,
    estimate_distance_m,
    landmarks_visible,
)

W, H = DEFAULT_FRAME_SIZE


# ---------------------------------------------------------------------------
# Helpers: landmark ficticio y modelo antropométrico
# ---------------------------------------------------------------------------

class _LM:
    """Simula un NormalizedLandmark de MediaPipe."""

    def __init__(self, x: float, y: float, z: float = 0.0,
                 visibility: float = 1.0):
        self.x = x
        self.y = y
        self.z = z
        self.visibility = visibility

    def __repr__(self):
        return f"LM(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f})"


def _make_pose(ear_lateral_cm: float = 7.0,
               neck_cm: float = 22.0,
               forward_cm: float = 0.0,
               shoulder_roll_deg: float = 0.0,
               px_per_cm: float = 6.0,
               visibility: float = 1.0) -> list:
    """
    Construye 33 landmarks a partir de medidas anatómicas en centímetros.

    Parameters
    ----------
    ear_lateral_cm:
        Separación de cada oreja respecto al plano medio sagital.
    neck_cm:
        Altura del punto medio inter-auricular sobre la línea de hombros.
    forward_cm:
        Desplazamiento anterior de la cabeza (cabeza adelantada).
    shoulder_roll_deg:
        Inclinación real de la línea de hombros.
    px_per_cm:
        Escala de proyección; 6 px/cm corresponde aproximadamente a un sujeto
        a 0.6 m de una webcam de 60° de FOV.
    """
    cx, cy = W / 2.0, H * 0.75
    half_shoulder_px = 19.0 * px_per_cm          # biacromial 38 cm
    r = math.radians(shoulder_roll_deg)

    sxl = cx - half_shoulder_px * math.cos(r)
    syl = cy - half_shoulder_px * math.sin(r)
    sxr = cx + half_shoulder_px * math.cos(r)
    syr = cy + half_shoulder_px * math.sin(r)

    ear_y = cy - neck_cm * px_per_cm
    # z de MediaPipe: escala de x, y decrece hacia la cámara.
    ear_z = -forward_cm * px_per_cm / W

    lms = [_LM(0.5, 0.5, 0.0) for _ in range(33)]
    lms[7]  = _LM((cx - ear_lateral_cm * px_per_cm) / W, ear_y / H, ear_z, visibility)
    lms[8]  = _LM((cx + ear_lateral_cm * px_per_cm) / W, ear_y / H, ear_z, visibility)
    lms[11] = _LM(sxl / W, syl / H, 0.0, visibility)
    lms[12] = _LM(sxr / W, syr / H, 0.0, visibility)
    return lms


def _make_eye(ear_value: float, width_px: float = 30.0,
              cx: float = 300.0, cy: float = 240.0) -> list:
    """
    Construye 478 landmarks con un ojo de EAR conocido **en píxeles**.

    Los 6 puntos van en el orden de Soukupová & Čech
    [P1 externo, P2-P3 párpado sup., P4 interno, P5-P6 párpado inf.].
    """
    h = ear_value * width_px
    lms = [_LM(0.0, 0.0) for _ in range(478)]
    lms[0] = _LM((cx - width_px / 2) / W, cy / H)
    lms[1] = _LM((cx - width_px / 4) / W, (cy - h / 2) / H)
    lms[2] = _LM((cx + width_px / 4) / W, (cy - h / 2) / H)
    lms[3] = _LM((cx + width_px / 2) / W, cy / H)
    lms[4] = _LM((cx + width_px / 4) / W, (cy + h / 2) / H)
    lms[5] = _LM((cx - width_px / 4) / W, (cy + h / 2) / H)
    return lms


def _make_mouth(mar: float, width_px: float = 50.0,
                cx: float = 320.0, cy: float = 300.0) -> list:
    """Construye landmarks faciales con un MAR conocido en píxeles."""
    h = mar * width_px
    lms = [_LM(0.5, 0.5) for _ in range(478)]
    lms[13]  = _LM(cx / W, (cy - h / 2) / H)     # labio superior interno
    lms[14]  = _LM(cx / W, (cy + h / 2) / H)     # labio inferior interno
    lms[61]  = _LM((cx - width_px / 2) / W, cy / H)   # comisura izquierda
    lms[291] = _LM((cx + width_px / 2) / W, cy / H)   # comisura derecha
    return lms


# ---------------------------------------------------------------------------
# Ángulo Cervical (θc)
# ---------------------------------------------------------------------------

class TestCervicalAngle:

    def test_neutral_posture_is_zero(self):
        """Postura neutra (cabeza sobre los hombros) → θc ≈ 0°."""
        angle = calculate_cervical_angle(_make_pose())
        assert angle < 1.0, f"Postura neutra debería dar θc ≈ 0°, obtuvo {angle:.2f}°"

    @pytest.mark.parametrize("lateral_cm", [0.0, 4.0, 7.0, 9.0, 11.0])
    def test_lateral_ear_offset_does_not_bias_angle(self, lateral_cm):
        """
        REGRESIÓN — el sesgo que hacía inservible la métrica.

        Usando solo P7 (oreja izquierda) como referencia de la cabeza, la
        separación lateral de la oreja respecto al plano medio inclina el
        vector cervical y θc arranca en ~13° con postura perfecta; en la
        sesión real del 2026-07-12 la mediana en postura normal fue 41°, por
        encima del umbral clínico de 30°, así que el sistema habría alertado
        de forma permanente. Con el punto medio inter-auricular, θc no depende
        de cuán separadas estén las orejas.
        """
        lms = _make_pose(ear_lateral_cm=lateral_cm)

        angle_ok = calculate_cervical_angle(lms, use_ear_midpoint=True)
        assert angle_ok < 1.0, (
            f"Con orejas a {lateral_cm} cm del plano medio y postura neutra, "
            f"θc debería seguir siendo ~0°, obtuvo {angle_ok:.2f}°"
        )

        angle_legacy = calculate_cervical_angle(lms, use_ear_midpoint=False)
        if lateral_cm >= 7.0:
            assert angle_legacy > 10.0, (
                "La formulación de una sola oreja debe seguir mostrando el "
                "sesgo (se conserva para cuantificarlo en el Capítulo IV)"
            )

    @pytest.mark.parametrize("forward_cm,expected_deg", [
        (0.0, 0.0), (3.0, 7.8), (6.0, 15.3), (9.0, 22.3), (12.0, 28.6),
    ])
    def test_scale_matches_forward_displacement(self, forward_cm, expected_deg):
        """
        θc debe crecer con el desplazamiento anterior real de la cabeza y con
        una escala anatómicamente sensata: ~12 cm de cabeza adelantada quedan
        justo en el umbral de 30° fijado por el experto.
        """
        angle = calculate_cervical_angle(_make_pose(forward_cm=forward_cm))
        assert abs(angle - expected_deg) < 1.0, (
            f"{forward_cm} cm de cabeza adelantada → esperado ~{expected_deg}°, "
            f"obtuvo {angle:.2f}°"
        )

    def test_monotonic_in_forward_displacement(self):
        """θc debe ser estrictamente creciente con la cabeza adelantada."""
        angles = [calculate_cervical_angle(_make_pose(forward_cm=d))
                  for d in (0, 2, 4, 6, 8, 10, 12)]
        assert all(b > a for a, b in zip(angles, angles[1:])), angles

    def test_invariant_to_subject_distance(self):
        """
        θc es un ángulo: no debe depender de lo cerca que esté el sujeto.
        Sin esto, acercarse a la cámara cambiaría la lectura sin que el
        usuario haya movido el cuello.
        """
        angles = [calculate_cervical_angle(
            _make_pose(forward_cm=8.0, px_per_cm=p)) for p in (4.0, 6.0, 9.0)]
        assert max(angles) - min(angles) < 0.5, angles

    def test_components_separate_sagittal_and_lateral(self):
        """La componente sagital captura la cabeza adelantada; la lateral, 0."""
        theta_c, sag, lat = calculate_cervical_components(_make_pose(forward_cm=9.0))
        assert sag > 20.0, f"Componente sagital esperada >20°, obtuvo {sag:.2f}°"
        assert abs(lat) < 1.0, f"Sin inclinación lateral, obtuvo {lat:.2f}°"
        assert abs(theta_c - sag) < 0.5, "Sin componente lateral, θc ≈ sagital"

    def test_raises_insufficient_landmarks(self):
        with pytest.raises(ValueError, match="landmarks"):
            calculate_cervical_angle([_LM(0, 0)] * 5)

    def test_angle_in_valid_range(self):
        """θc siempre en [0°, 180°] ante entradas arbitrarias."""
        import random
        random.seed(42)
        for _ in range(50):
            lms = _make_pose(
                ear_lateral_cm=random.uniform(0, 12),
                neck_cm=random.uniform(5, 35),
                forward_cm=random.uniform(-10, 25),
                shoulder_roll_deg=random.uniform(-25, 25),
            )
            angle = calculate_cervical_angle(lms)
            assert 0.0 <= angle <= 180.0

    def test_superimposed_landmarks_return_zero(self):
        """Landmarks superpuestos → 0.0, sin división por cero."""
        lms = [_LM(0.5, 0.5, 0.0) for _ in range(33)]
        assert calculate_cervical_angle(lms) == 0.0


# ---------------------------------------------------------------------------
# Asimetría Escapular (ΔE)
# ---------------------------------------------------------------------------

class TestShoulderAsymmetry:

    def test_level_shoulders(self):
        assert abs(calculate_shoulder_asymmetry(_make_pose())) < 0.5

    @pytest.mark.parametrize("roll_deg", [5.0, 10.0, 15.0, 20.0])
    def test_measured_angle_matches_real_angle(self, roll_deg):
        """
        REGRESIÓN — error de escala del 33%.

        Sobre coordenadas normalizadas de un frame 640x480, atan2 mezcla dos
        escalas distintas y la tangente queda multiplicada por W/H = 1.333:
        una inclinación real de 10° se reportaba como 13.2°. Como el umbral
        del experto (10°) se compara contra ese número, el sistema alertaba en
        realidad a partir de 7.5° reales.
        """
        measured = abs(calculate_shoulder_asymmetry(
            _make_pose(shoulder_roll_deg=roll_deg)))
        assert abs(measured - roll_deg) < 0.5, (
            f"Inclinación real {roll_deg}° → medida {measured:.2f}°"
        )

    def test_sign_is_independent_of_which_shoulder_is_left(self):
        """
        El signo debe venir de la inclinación, no de qué hombro cae a la
        izquierda de la imagen (que cambiaría al espejar el frame).
        """
        lms = _make_pose(shoulder_roll_deg=10.0)
        lms[11], lms[12] = lms[12], lms[11]
        assert abs(abs(calculate_shoulder_asymmetry(lms)) - 10.0) < 0.5

    def test_raises_insufficient_landmarks(self):
        with pytest.raises(ValueError, match="landmarks"):
            calculate_shoulder_asymmetry([_LM(0, 0)] * 5)


# ---------------------------------------------------------------------------
# EAR
# ---------------------------------------------------------------------------

class TestEAR:

    EYE = [0, 1, 2, 3, 4, 5]

    @pytest.mark.parametrize("ear_value", [0.35, 0.28, 0.21, 0.15, 0.05])
    def test_ear_matches_pixel_space_ratio(self, ear_value):
        """
        REGRESIÓN — el EAR estaba inflado por W/H = 1.333.

        El umbral de fatiga de la literatura (0.21, Soukupová & Čech) está
        definido sobre coordenadas en píxeles. Calculado sobre coordenadas
        normalizadas de un frame 640x480, un ojo con EAR real 0.21 medía 0.28,
        de modo que exigir "medido ≤ 0.21" equivalía a exigir un EAR real de
        0.157: el ojo casi cerrado del todo. La detección de fatiga ocular era
        prácticamente inalcanzable.
        """
        measured = calculate_ear(_make_eye(ear_value), self.EYE)
        assert abs(measured - ear_value) < 0.005, (
            f"EAR real {ear_value:.3f} → medido {measured:.3f}"
        )

    def test_closed_eye_below_threshold(self):
        assert calculate_ear(_make_eye(0.08), self.EYE) < 0.21

    def test_open_eye_above_threshold(self):
        assert calculate_ear(_make_eye(0.32), self.EYE) > 0.21

    def test_avg_ear_is_mean_of_both_eyes(self):
        lms = _make_eye(0.30)
        right = [10, 11, 12, 13, 14, 15]
        other = _make_eye(0.20, cx=400.0)
        for dst, src in zip(right, [0, 1, 2, 3, 4, 5]):
            lms[dst] = other[src]
        avg = calculate_avg_ear(lms, self.EYE, right)
        assert abs(avg - 0.25) < 0.01, f"Promedio esperado 0.25, obtuvo {avg:.3f}"

    def test_raises_wrong_index_count(self):
        with pytest.raises(ValueError, match="6 elementos"):
            calculate_ear(_make_eye(0.3), [0, 1, 2])

    def test_degenerate_eye_returns_zero(self):
        """Ancho de ojo nulo → 0.0, sin división por cero."""
        lms = [_LM(0.5, 0.5) for _ in range(478)]
        assert calculate_ear(lms, self.EYE) == 0.0


# ---------------------------------------------------------------------------
# Apertura bucal (MAR)
# ---------------------------------------------------------------------------

class TestMouthOpening:

    def test_closed_mouth(self):
        assert calculate_mouth_opening(_make_mouth(0.0)) < 0.05

    @pytest.mark.parametrize("mar", [0.10, 0.30, 0.45, 0.60])
    def test_mar_matches_pixel_space_ratio(self, mar):
        """REGRESIÓN — mismo factor W/H = 1.333 que afectaba al EAR."""
        measured = calculate_mouth_opening(_make_mouth(mar))
        assert abs(measured - mar) < 0.005, f"MAR real {mar} → medido {measured:.3f}"

    def test_yawn_above_threshold(self):
        assert calculate_mouth_opening(_make_mouth(0.55)) >= 0.45

    def test_degenerate_mouth_returns_zero(self):
        lms = [_LM(0.5, 0.5) for _ in range(478)]
        assert calculate_mouth_opening(lms) == 0.0


# ---------------------------------------------------------------------------
# Distancia y visibilidad
# ---------------------------------------------------------------------------

class TestDistanceEstimation:

    @staticmethod
    def _px_per_cm_for(distance_m: float, hfov_deg: float = 60.0) -> float:
        """px/cm que produce un sujeto a `distance_m` con ese FOV."""
        focal_px = (W / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        return (0.38 * focal_px / distance_m) / 38.0

    @pytest.mark.parametrize("distance_m", [0.40, 0.50, 0.60, 0.70, 0.90, 1.50])
    def test_recovers_known_distance(self, distance_m):
        """
        La estimación debe recuperar la distancia con la que se generó la
        proyección. Es lo que permite avisar al usuario cuando se sale del
        rango 0.50–0.70 m validado por el experto.
        """
        lms = _make_pose(px_per_cm=self._px_per_cm_for(distance_m))
        estimated = estimate_distance_m(lms)
        assert estimated is not None
        assert abs(estimated - distance_m) < 0.02, (
            f"Distancia real {distance_m} m → estimada {estimated:.3f} m"
        )

    def test_returns_none_without_landmarks(self):
        assert estimate_distance_m([_LM(0, 0)] * 5) is None

    def test_returns_none_when_shoulders_collapsed(self):
        lms = [_LM(0.5, 0.5) for _ in range(33)]
        assert estimate_distance_m(lms) is None


class TestLandmarkVisibility:

    INDICES = (7, 8, 11, 12)

    def test_all_visible(self):
        assert landmarks_visible(_make_pose(visibility=0.9), self.INDICES)

    def test_low_visibility_rejected(self):
        """
        Landmarks con visibilidad baja son inferidos, no observados: medir un
        ángulo sobre ellos produce un número inventado que el FSM no debe
        acumular como tiempo en postura de riesgo.
        """
        assert not landmarks_visible(_make_pose(visibility=0.2), self.INDICES)

    def test_empty_list_rejected(self):
        assert not landmarks_visible([], self.INDICES)

    def test_out_of_range_index_rejected(self):
        assert not landmarks_visible(_make_pose(), (7, 8, 99))
