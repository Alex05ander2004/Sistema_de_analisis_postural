"""Tests del filtrado temporal (src/vision/smoothing.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vision.smoothing import MetricSmoother, MetricSmootherBank


class TestMetricSmoother:

    def test_first_sample_passes_through(self):
        """
        Sin transitorio de arranque: la primera lectura no debe salir a medio
        camino entre 0 y el valor real, o el panel mostraría un θc falso
        durante el primer segundo de cada sesión.
        """
        s = MetricSmoother()
        assert s.update(30.0) == 30.0

    def test_converges_to_constant_input(self):
        s = MetricSmoother(alpha=0.35)
        for _ in range(50):
            out = s.update(25.0)
        assert abs(out - 25.0) < 0.01

    def test_rejects_single_frame_outlier(self):
        """
        Un frame mal estimado no debe llegar al FSM. En la sesión real, ΔE
        saltaba a -20° en frames sueltos con el sujeto quieto; sin mediana,
        ese pico cruzaría el umbral de 10° y arrancaría un temporizador.
        """
        s = MetricSmoother(median_window=5, alpha=0.5)
        for _ in range(10):
            s.update(3.0)
        out = s.update(-20.0)          # outlier de un solo frame
        assert abs(out - 3.0) < 1.0, (
            f"Un outlier aislado no debe mover la salida, obtuvo {out:.2f}"
        )

    def test_follows_a_real_sustained_change(self):
        """Un cambio real de postura sí debe reflejarse."""
        s = MetricSmoother(median_window=5, alpha=0.35)
        for _ in range(10):
            s.update(5.0)
        for _ in range(30):
            out = s.update(35.0)
        assert abs(out - 35.0) < 1.0

    def test_attenuates_threshold_chattering(self):
        """
        Una señal que oscila alrededor del umbral debe salir estabilizada.
        Sin esto la condición se activa y desactiva a 20-30 Hz, el
        temporizador se reinicia sin parar y la alerta nunca se confirma
        aunque la postura de riesgo sea real.
        """
        s = MetricSmoother(median_window=5, alpha=0.3)
        outputs = []
        for i in range(60):
            outputs.append(s.update(30.0 + (6.0 if i % 2 == 0 else -6.0)))
        tail = outputs[20:]
        assert max(tail) - min(tail) < 3.0, (
            f"Entrada oscilando ±6° -> salida con rango {max(tail) - min(tail):.2f}°"
        )

    def test_reset_clears_state(self):
        s = MetricSmoother()
        for _ in range(10):
            s.update(40.0)
        s.reset()
        assert s.value is None
        assert s.update(5.0) == 5.0

    def test_value_is_none_before_first_sample(self):
        assert MetricSmoother().value is None

    @pytest.mark.parametrize("median_window,alpha", [(0, 0.5), (5, 0.0), (5, 1.5)])
    def test_rejects_invalid_parameters(self, median_window, alpha):
        with pytest.raises(ValueError):
            MetricSmoother(median_window=median_window, alpha=alpha)

    def test_alpha_one_is_pure_median(self):
        s = MetricSmoother(median_window=3, alpha=1.0)
        s.update(1.0)
        s.update(2.0)
        assert s.update(3.0) == 2.0


class TestMetricSmootherBank:

    def test_channels_are_independent(self):
        bank = MetricSmootherBank(["theta_c", "ear"], median_window=1, alpha=1.0)
        assert bank.update("theta_c", 30.0) == 30.0
        assert bank.update("ear", 0.25) == 0.25
        assert bank.get("theta_c") == 30.0

    def test_reset_single_channel(self):
        bank = MetricSmootherBank(["a", "b"])
        bank.update("a", 1.0)
        bank.update("b", 2.0)
        bank.reset("a")
        assert bank.get("a") is None
        assert bank.get("b") == 2.0

    def test_reset_all_channels(self):
        bank = MetricSmootherBank(["a", "b"])
        bank.update("a", 1.0)
        bank.update("b", 2.0)
        bank.reset()
        assert bank.get("a") is None
        assert bank.get("b") is None
