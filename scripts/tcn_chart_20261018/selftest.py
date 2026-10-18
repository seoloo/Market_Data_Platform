"""Run in isolated ML environment, plus production pytest contract tests."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from scripts.tcn_chart_20261018 import account, execution, models


class ResearchTests(unittest.TestCase):
    def fixture(self) -> tuple[Any, Any]:
        import numpy as np

        a = np.tile([0, 100, 101, 99, 100, 100000], (1600, 1)).astype(float)
        a[:, 0] = np.arange(1600) * 60000
        return a, np.asarray(
            [
                [8 * 3600000, 0.0001, 100, 99, 101],
                [16 * 3600000, 0.0001, 100, 99, 101],
                [24 * 3600000, 0.0001, 100, 99, 101],
            ]
        )

    def test_cash_funding_and_replay(self) -> None:
        a, f = self.fixture()
        for side in (1, -1):
            e = execution.simulate(a, f, 0, side, "ETHUSDT")
            self.assertEqual(e.status, "CLOSED")
            self.assertEqual(e.end - e.start, 1440)
            self.assertEqual(e.funding, -Decimal(side) * e.qty * Decimal("0.03"))
            self.assertEqual(e.gross, e.qty * Decimal("-0.10"))
            pnl, _ = execution.equity_path(e, a, side)
            self.assertAlmostEqual(pnl[-1], float(e.net), places=8)

    def test_stop_and_gap(self) -> None:
        a, f = self.fixture()
        a[100, 3] = 90
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertTrue(e.stopped)
        self.assertEqual(e.end, 100)
        a[100, 1] = 91
        g = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertLess(g.net, e.net)

    def test_partial_and_unresolved(self) -> None:
        a, f = self.fixture()
        a[0, 5] = 2000
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertTrue(e.partial)
        self.assertEqual(e.qty, Decimal("2.000"))
        a[1440:, 5] = 0
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertEqual(e.status, "UNRESOLVED")
        self.assertGreater(e.remaining, 0)

    def test_training_only_scaler_and_causal_tcn(self) -> None:
        import numpy as np
        import torch

        a = np.ones((10, 96, 10), dtype=np.float32)
        mu, sd = models.scaler(a)
        models.normalize(a * 10000, mu, sd)
        np.testing.assert_array_equal(mu, np.ones(10))
        torch.manual_seed(1)
        model = models.network().eval()
        x = torch.randn(2, 10, 96)
        with torch.no_grad():
            before = model(x)
            x[:, :, 70:] += 100
            after = model(x)
        torch.testing.assert_close(before[:, :, :70], after[:, :, :70], rtol=0, atol=0)
        self.assertLess(sum(p.numel() for p in model.parameters()), 20000)

    def test_stop_minute_funding_and_time_priority(self) -> None:
        import numpy as np

        a, f = self.fixture()
        a[1441, 3] = 90
        # The funding timestamp is inside the stop minute, rather than at its open.
        f = np.asarray([[1441 * 60000 + 1000, 0.01, 100, 100, 100]])
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertTrue(e.stopped)
        self.assertLess(e.funding, 0)
        f[0, 1] = -0.01
        credit = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertEqual(credit.funding, 0)

    def test_shared_wallet_cash_and_no_overlap(self) -> None:
        import numpy as np

        a, f = self.fixture()
        ds = {
            "times": np.asarray([0, 0, 4 * 3600000]),
            "splits": np.asarray([0, 0, 0]),
            "symbols": np.asarray([0, 1, 0]),
        }
        assets = {s: {"minutes": a, "funding": f} for s in ("BTCUSDT", "ETHUSDT")}
        with patch.object(account, "BOUNDS", ((0, 86400000 * 2),)):
            r = account.replay(ds, np.asarray([1, 2, 1]), 0, assets)
        self.assertEqual(r["n_trades"], 1)
        self.assertEqual(r["veto"]["POSITION_OR_60MIN"], 2)
        self.assertAlmostEqual(r["net"], r["gross"] - r["fee"] + r["funding"], places=8)
        self.assertLessEqual(r["trades"][0]["entry_notional"], 1000)


if __name__ == "__main__":
    unittest.main()
