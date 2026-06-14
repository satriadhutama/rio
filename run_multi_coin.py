"""run_multi_coin.py — Orkestrator pipeline backtest untuk 10 coin top.

Coin: BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, TRX.

Untuk tiap coin, urutan kerja:
    1. Baca OHLCV + funding dari `data/deriv/{ohlcv,funding}/{COIN}.parquet`
    2. indicators.compute_all_indicators()
    3. strategy_trend.compute_signals()
    4. backtest_engine.run_backtest() -> (trades, equity)
    5. metrics.compute_metrics()
    6. Tulis trades & equity per-coin ke `data/backtest/{COIN}_{trades,equity}.parquet`.

Di akhir: ringkasan disusun jadi DataFrame, disimpan ke
`data/backtest/summary.parquet`, dan dicetak sebagai tabel ke stdout
plus baris AGGREGATE.

ASUMSI MODAL: Setiap coin diberi modal terpisah USD 10.000 — BUKAN
modal bersama yang di-share antar coin. Karena itu, jumlah total
return semua coin ditampilkan sebagai sum (bukan rata-rata): setiap
coin berdiri sendiri secara akuntansi.

Cara pakai:
    python run_multi_coin.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import indicators
import strategy_trend
import backtest_engine
import metrics


COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "TRX"]
INITIAL_CAPITAL = 10_000.0

OHLCV_DIR = Path("data") / "deriv" / "ohlcv"
FUNDING_DIR = Path("data") / "deriv" / "funding"
OUTPUT_DIR = Path("data") / "backtest"


# ---------------------------------------------------------------------------
# Pipeline per coin
# ---------------------------------------------------------------------------

def process_coin(coin: str) -> dict:
    """Jalankan pipeline penuh untuk satu coin; kembalikan dict metrik."""
    print(f"Processing {coin}...")

    ohlcv = pd.read_parquet(OHLCV_DIR / f"{coin}.parquet")
    funding = pd.read_parquet(FUNDING_DIR / f"{coin}.parquet")

    ind = indicators.compute_all_indicators(ohlcv)
    sig = strategy_trend.compute_signals(ind)
    trades, equity = backtest_engine.run_backtest(
        sig, funding, initial_capital=INITIAL_CAPITAL,
    )
    m = metrics.compute_metrics(trades, equity, INITIAL_CAPITAL)

    trades.to_parquet(OUTPUT_DIR / f"{coin}_trades.parquet", index=False)
    equity.to_parquet(OUTPUT_DIR / f"{coin}_equity.parquet", index=False)

    n = int(m["total_trades"])
    ret = m["total_return_pct"]
    sharpe = m["sharpe"]
    print(f"Done {coin}: {n} trades, return {ret:+.2f}%, sharpe {sharpe:.2f}")

    m["coin"] = coin
    return m


# ---------------------------------------------------------------------------
# Pemformat angka untuk tabel
# ---------------------------------------------------------------------------

def _pct(value, decimals: int = 2, signed: bool = False) -> str:
    """Format angka jadi persen (e.g. '12.34%' atau '+12.34%'). NaN/inf -> '---'."""
    if pd.isna(value) or not np.isfinite(value):
        return "---"
    sign = "+" if signed else ""
    return f"{value:{sign}.{decimals}f}%"


def _num(value, decimals: int = 2) -> str:
    """Format angka biasa. NaN/inf -> '---'."""
    if pd.isna(value) or not np.isfinite(value):
        return "---"
    return f"{value:.{decimals}f}"


# ---------------------------------------------------------------------------
# Cetak ringkasan + agregat
# ---------------------------------------------------------------------------

def print_summary(summary_df: pd.DataFrame) -> None:
    """Cetak tabel ringkasan + baris AGGREGATE ke stdout."""
    # Lebar kolom dipilih agar muat label "AGGREGATE" di kolom pertama.
    w = (10, 8, 9, 9, 11, 10, 12, 8)

    header = (
        f"{'coin':<{w[0]}}{'trades':>{w[1]}}{'win%':>{w[2]}}"
        f"{'PF':>{w[3]}}{'ret%':>{w[4]}}{'CAGR%':>{w[5]}}"
        f"{'maxDD%':>{w[6]}}{'Sharpe':>{w[7]}}"
    )
    sep = "-" * len(header)

    print()
    print(sep)
    print(header)
    print(sep)
    for _, r in summary_df.iterrows():
        # win_rate dan cagr datang sebagai desimal (0..1) → kali 100.
        line = (
            f"{r['coin']:<{w[0]}}"
            f"{int(r['total_trades']):>{w[1]}d}"
            f"{_pct(r['win_rate'] * 100, 1):>{w[2]}}"
            f"{_num(r['profit_factor']):>{w[3]}}"
            f"{_pct(r['total_return_pct'], signed=True):>{w[4]}}"
            f"{_pct(r['cagr'] * 100, signed=True):>{w[5]}}"
            f"{_pct(r['max_drawdown_pct']):>{w[6]}}"
            f"{_num(r['sharpe']):>{w[7]}}"
        )
        print(line)
    print(sep)

    # ---- Hitung agregat ----
    trades_col = summary_df["total_trades"].astype(float)
    win_col = summary_df["win_rate"].astype(float)

    total_trades_all = int(trades_col.sum())

    mask = (trades_col > 0) & win_col.notna()
    if mask.any() and trades_col[mask].sum() > 0:
        wavg_win = float(
            (win_col[mask] * trades_col[mask]).sum() / trades_col[mask].sum()
        )
    else:
        wavg_win = float("nan")

    median_sharpe = float(summary_df["sharpe"].median(skipna=True))
    sum_return = float(summary_df["total_return_pct"].sum(skipna=True))

    agg_line = (
        f"{'AGGREGATE':<{w[0]}}"
        f"{total_trades_all:>{w[1]}d}"
        f"{_pct(wavg_win * 100, 1):>{w[2]}}"
        f"{'---':>{w[3]}}"
        f"{_pct(sum_return, signed=True):>{w[4]}}"
        f"{'---':>{w[5]}}"
        f"{'---':>{w[6]}}"
        f"{_num(median_sharpe):>{w[7]}}"
    )
    print(agg_line)
    print(sep)
    print(
        f"AGGREGATE: total_trades={total_trades_all}, "
        f"weighted_win_rate={_pct(wavg_win * 100)}, "
        f"median_sharpe={_num(median_sharpe)}, "
        f"sum_total_return_pct={_pct(sum_return, signed=True)}"
    )
    print(f"(Modal ${INITIAL_CAPITAL:,.0f} terpisah per coin.)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []
    for coin in COINS:
        m = process_coin(coin)
        summary_rows.append(m)

    summary_df = pd.DataFrame(summary_rows)
    # Susun ulang: kolom 'coin' di paling depan.
    cols = ["coin"] + [c for c in summary_df.columns if c != "coin"]
    summary_df = summary_df[cols]

    summary_df.to_parquet(OUTPUT_DIR / "summary.parquet", index=False)

    print_summary(summary_df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
