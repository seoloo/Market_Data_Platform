"""Run in isolated ML environment, plus production pytest contract tests."""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from scripts.tcn_chart_20261018 import account, data, dataset, execution, metrics, models


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

    def test_unresolved_inventory_not_deleted(self) -> None:
        import numpy as np

        a, f = self.fixture()
        a = np.concatenate([a, a[:1400].copy()])
        a[:, 0] = np.arange(len(a)) * 60000
        a[1440:, 5] = 0
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        self.assertEqual(e.status, "UNRESOLVED")
        qty = e.remaining
        execution.carry_unresolved(e, a, f, 1, 2 * 86400000)
        self.assertEqual(e.status, "UNRESOLVED_MTM")
        self.assertEqual(e.remaining, qty)
        self.assertTrue(e.was_unresolved)
        a[1700, 3] = 90
        e = execution.simulate(a, f, 0, 1, "ETHUSDT")
        execution.carry_unresolved(e, a, f, 1, 2 * 86400000)
        self.assertEqual(e.status, "LATE_EMERGENCY_CLOSED")
        self.assertEqual(e.end, 1700)
        pnl, _ = execution.equity_path(e, a, 1)
        self.assertAlmostEqual(pnl[-1], float(e.net), places=8)

    def test_unknown_labels_keep_prediction_rows(self) -> None:
        import numpy as np

        bars = np.tile([1735689600000, 100, 101, 99, 100, 1000], (100, 1)).astype(float)
        bars[:, 0] += np.arange(100) * 14400000
        fake = {"minutes": bars, "h4": bars, "funding": bars}
        with (
            TemporaryDirectory() as folder,
            patch.object(data, "OUT", Path(folder)),
            patch.object(data, "load", return_value=fake),
            patch.object(dataset.features, "calculate", return_value=[[0] * 10] * 100),
            patch.object(
                execution, "simulate", return_value=execution.Episode(status="UNRESOLVED")
            ),
        ):
            ds = dataset.build()
        self.assertEqual(len(ds["x"]), 10)
        self.assertTrue(np.all(ds["y"] == -1))

    def test_account_terminal_inventory_and_reserve(self) -> None:
        import numpy as np

        a, f = self.fixture()
        a = np.concatenate([a, a[:1400].copy()])
        a[:, 0] = np.arange(len(a)) * 60000
        a[1440:, 5] = 0
        a[1700:, 1:5] = [120, 121, 119, 120]
        ds = {"times": np.asarray([0]), "splits": np.asarray([0]), "symbols": np.asarray([0])}
        assets = {"BTCUSDT": {"minutes": a, "funding": f}}
        with patch.object(account, "BOUNDS", ((0, 86400000 * 2),)):
            r = account.replay(ds, np.asarray([1]), 0, assets)
        self.assertEqual(r["unresolved"], 1)
        self.assertEqual(r["terminal_open"], 1)
        self.assertGreater(r["trades"][0]["fee_reserve"], 0)
        self.assertGreater(Decimal(r["trades"][0]["remaining_qty"]), 0)
        self.assertAlmostEqual(r["final"], r["daily_equity"][-1], places=8)
        self.assertAlmostEqual(r["net"], r["gross"] - r["fee"] + r["funding"], places=8)

    def test_probability_tie_is_flat(self) -> None:
        import numpy as np

        out = metrics.decisions(np.asarray([[0.1, 0.45, 0.45], [0.1, 0.2, 0.7]]))
        np.testing.assert_array_equal(out, [0, 2])


if __name__ == "__main__":
    unittest.main()
