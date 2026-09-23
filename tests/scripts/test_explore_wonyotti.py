"""워뇨띠 탐색 스크립트의 집계 규칙(단위·집중도·청산 표시) 검사."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.explore_wonyotti import executions, wallet

WALLET_HEADER = "date,transactid,account,currency,amount,transactstatus,transacttype\n"
EXEC_HEADER = (
    "date,exectype,symbol,ordtype,orderid,lastliquidityind,text,execinst,settlcurrency,execcomm\n"
)


def test_wallet_units_and_concentration(tmp_path: Path) -> None:
    rows = [
        "2021-01-01,a,aoa,XBt,100000000,Completed,RealisedPNL",
        "2021-01-01,b,aoa,XBt,50000000,Completed,RealisedPNL",
        "2021-02-01,c,aoa,XBt,-30000000,Completed,RealisedPNL",
        "2021-02-02,d,aoa,XBt,999,Pending,RealisedPNL",
        "2021-02-03,e,aoa,XBt,-100000000,Completed,Withdrawal",
    ]
    path = tmp_path / "wallet.csv"
    path.write_text(WALLET_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    result = wallet(path)
    assert result["realised_pnl_btc_total"] == pytest.approx(1.2)
    assert result["pnl_days"] == 2
    assert result["positive_months"] == 1
    assert result["months"] == 2
    assert result["btc_by_year_type"] == {"2021/RealisedPNL": 1.2, "2021/Withdrawal": -1.0}


def test_executions_count_orders_liquidity_and_liquidations(tmp_path: Path) -> None:
    rows = [
        "2019-09-24,Trade,XBTUSD,StopLimit,o1,RemovedLiquidity,Liquidation,,XBt,1000",
        "2019-09-24,Trade,XBTUSD,Limit,o2,AddedLiquidity,,,XBt,-500",
        "2019-09-24,Trade,XBTUSD,Limit,o2,AddedLiquidity,,,XBt,-500",
        "2019-09-24,Funding,XBTUSD,Limit,,,,,XBt,",
    ]
    (tmp_path / "aoa-execution-x.csv").write_text(
        EXEC_HEADER + "\n".join(rows) + "\n", encoding="utf-8"
    )
    result = executions(tmp_path)
    assert result["rows"] == 4
    assert result["exec_types"] == {"2019": {"Funding": 1, "Trade": 3}}
    assert result["ord_types_orders"] == {"2019/Limit": 1, "2019/StopLimit": 1}
    assert result["liquidation_fills"] == {"2019": {"XBTUSD": 1}}
    assert result["liquidity_fills"] == {"2019": {"AddedLiquidity": 2, "RemovedLiquidity": 1}}
    assert result["commission_btc_xbt_settled"] == {"2019": 0.0}
