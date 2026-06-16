"""run_multi_coin_v2_1h.py — Orchestrator backtest v2 untuk timeframe 1H.

Copy logika dari `run_multi_coin_v2.py` dengan perbedaan HANYA:
  - import strategy_trend_v2_1h (yang re-export compute_signals_v2)
  - data path: data/deriv/ohlcv_1h, funding_1h, oi_1h
  - output path: outputs/trades_v2_1h.parquet dll.

Parameter strategi & engine PERSIS SAMA (tidak di-tune).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import indicators
import strategy_trend_v2_1h
import backtest_engine
import metrics


COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "TRX"]
INITIAL_CAPITAL = 10_000.0

OHLCV_DIR = Path("data/deriv/ohlcv_1h")
FUNDING_DIR = Path("data/deriv/funding_1h")
OI_DIR = Path("data/deriv/oi_1h")
OUTPUT_DIR = Path("outputs")

TRADES_OUT = OUTPUT_DIR / "trades_v2_1h.parquet"
EQUITY_OUT = OUTPUT_DIR / "equity_v2_1h.parquet"
SUMMARY_OUT = OUTPUT_DIR / "summary_v2_1h.parquet"


def process_coin(coin: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    print(f"Processing {coin}...", flush=True)
    ohlcv = pd.read_parquet(OHLCV_DIR / f"{coin}.parquet")
    funding = pd.read_parquet(FUNDING_DIR / f"{coin}.parquet")
    oi = pd.read_parquet(OI_DIR / f"{coin}.parquet")

    ind = indicators.compute_all_indicators(ohlcv)
    sig = strategy_trend_v2_1h.compute_signals_v2(ind, funding, oi)
    trades, equity = backtest_engine.run_backtest(
        sig, funding, initial_capital=INITIAL_CAPITAL,
    )
    m = metrics.compute_metrics(trades, equity, INITIAL_CAPITAL)

    trades = trades.copy()
    trades["coin"] = coin
    equity = equity.copy()
    equity["coin"] = coin
    if "entry_time" in trades.columns:
        trades = trades.rename(columns={"entry_time": "entry_date",
                                         "exit_time": "exit_date"})

    n = int(m["total_trades"])
    ret = m["total_return_pct"]
    sharpe = m["sharpe"]
    print(f"Done {coin}: {n} trades, return {ret:+.2f}%, sharpe {sharpe:.2f}",
          flush=True)

    m["coin"] = coin
    return trades, equity, m


def _pct(value, decimals: int = 2, signed: bool = False) -> str:
    if pd.isna(value) or not np.isfinite(value):
        return "---"
    sign = "+" if signed else ""
    return f"{value:{sign}.{decimals}f}%"


def _num(value, decimals: int = 2) -> str:
    if pd.isna(value) or not np.isfinite(value):
        return "---"
    return f"{value:.{decimals}f}"


def print_summary(summary_df: pd.DataFrame) -> None:
    w = (10, 8, 9, 9, 11, 10, 12, 8)
    header = (f"{'coin':<{w[0]}}{'trades':>{w[1]}}{'win%':>{w[2]}}"
              f"{'PF':>{w[3]}}{'ret%':>{w[4]}}{'CAGR%':>{w[5]}}"
              f"{'maxDD%':>{w[6]}}{'Sharpe':>{w[7]}}")
    sep = "-" * len(header)
    print()
    print(sep)
    print(header)
    print(sep)
    for _, r in summary_df.iterrows():
        print(f"{r['coin']:<{w[0]}}"
              f"{int(r['total_trades']):>{w[1]}d}"
              f"{_pct(r['win_rate'] * 100, 1):>{w[2]}}"
              f"{_num(r['profit_factor']):>{w[3]}}"
              f"{_pct(r['total_return_pct'], signed=True):>{w[4]}}"
              f"{_pct(r['cagr'] * 100, signed=True):>{w[5]}}"
              f"{_pct(r['max_drawdown_pct']):>{w[6]}}"
              f"{_num(r['sharpe']):>{w[7]}}")
    print(sep)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_trades = []
    all_equity = []
    summary_rows = []
    for coin in COINS:
        trades, equity, m = process_coin(coin)
        all_trades.append(trades)
        all_equity.append(equity)
        summary_rows.append(m)

    trades_df = pd.concat(all_trades, ignore_index=True)
    equity_df = pd.concat(all_equity, ignore_index=True)
    summary_df = pd.DataFrame(summary_rows)
    summary_df = summary_df[["coin"] + [c for c in summary_df.columns
                                         if c != "coin"]]

    trades_df.to_parquet(TRADES_OUT, index=False)
    equity_df.to_parquet(EQUITY_OUT, index=False)
    summary_df.to_parquet(SUMMARY_OUT, index=False)

    print_summary(summary_df)
    print(f"\nSaved:\n  {TRADES_OUT}\n  {EQUITY_OUT}\n  {SUMMARY_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
