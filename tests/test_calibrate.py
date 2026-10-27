"""The screenshot reader and the weight fit, on synthetic inputs. No network."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import calibrate  # noqa: E402


class CalibrateTests(unittest.TestCase):
    def test_reads_values_back_from_the_color_bar(self):
        ramp = np.linspace(0.0, 1.0, 101)
        bar = np.stack([ramp, 1.0 - ramp, np.full_like(ramp, 0.5)], axis=1)
        img = np.zeros((120, 40, 3))
        img[10:111, 2] = bar[::-1]
        img[10:111, 20:30] = bar[[100, 50, 0]][np.arange(101) * 3 // 101][:, None, :]
        with tempfile.TemporaryDirectory() as tmp:
            mpimg.imsave(Path(tmp) / "shot.png", img)
            manifest = {
                "image": "shot.png",
                "plot": {"top": 10, "bottom": 110},
                "price": {"top": 200.0, "bottom": 100.0},
                "colorbar": {"x": 2, "top": 10, "bottom": 110, "max_usd": 1000.0},
                "columns": [20, 29],
            }
            prices, usd = calibrate.read_screenshot(manifest, Path(tmp))
        self.assertAlmostEqual(prices[0], 200.0)
        self.assertAlmostEqual(prices[-1], 100.0)
        self.assertAlmostEqual(usd[5], 1000.0, delta=15.0)
        self.assertAlmostEqual(usd[50], 500.0, delta=15.0)
        self.assertAlmostEqual(usd[-5], 0.0, delta=15.0)

    def test_nnls_recovers_non_negative_weights(self):
        rng = np.random.default_rng(7)
        a = rng.random((60, 5))
        truth = np.array([0.0, 0.5, 0.0, 1.5, 0.25])
        fitted = calibrate.nnls(a, a @ truth)
        np.testing.assert_allclose(fitted, truth, atol=0.02)


if __name__ == "__main__":
    unittest.main()
