"""metrics.py — Metrik kinerja backtest.

Mengonsumsi trade log dan equity curve hasil `run_backtest`, menghasilkan
satu kamus berisi statistik distribusi trade dan rasio risiko/imbal hasil.

Modul ini PURE: tidak memodifikasi input, tidak ada I/O, tidak ada print.
Trades dengan `exit_reason == 'rejected_insufficient_margin'` diabaikan
dari semua perhitungan statistik trade.
"""

from __future__ import annotations

import math

import numpy as np  # noqa: F401 (digunakan implisit via pandas / NaN)
import pandas as pd


_REJECTED = "rejected_insufficient_margin"


# ---------------------------------------------------------------------------
# Helper: statistik distribusi trade
# ---------------------------------------------------------------------------

def _trade_stats(trades: pd.DataFrame) -> dict:
    """Statistik dari trade log yang sudah difilter dari rejected."""
    nan = float("nan")
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0,
            "win_rate": nan,
            "profit_factor": nan,
            "expectancy": nan,
            "avg_win": nan,
            "avg_loss": nan,
        }

    pnl = trades["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate = len(wins) / n
    expectancy = float(pnl.mean())
    avg_win = float(wins.mean()) if len(wins) else nan
    avg_loss = float(losses.mean()) if len(losses) else nan  # negatif

    sum_wins = float(wins.sum())
    sum_losses_abs = abs(float(losses.sum()))
    if sum_losses_abs == 0:
        # Tidak ada loss sama sekali.
        profit_factor = float("inf") if sum_wins > 0 else nan
    else:
        profit_factor = sum_wins / sum_losses_abs

    return {
        "total_trades": n,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


# ---------------------------------------------------------------------------
# Helper: statistik dari equity curve
# ---------------------------------------------------------------------------

def _equity_stats(equity_df: pd.DataFrame, initial_capital: float,
                  periods_per_year: int, risk_free_rate: float) -> dict:
    """Total return, CAGR, max DD, Sharpe, Sortino, Calmar dari equity."""
    nan = float("nan")
    empty = {
        "total_return_pct": nan,
        "cagr": nan,
        "max_drawdown_pct": nan,
        "sharpe": nan,
        "sortino": nan,
        "calmar": nan,
    }

    if equity_df is None or len(equity_df) == 0:
        return empty

    eq = equity_df["equity"].astype(float).reset_index(drop=True)
    ts = pd.to_datetime(equity_df["timestamp"]).reset_index(drop=True)

    end = float(eq.iloc[-1])
    if initial_capital > 0:
        total_return_pct = (end - initial_capital) / initial_capital * 100.0
    else:
        total_return_pct = nan

    # --- CAGR berdasarkan jumlah HARI nyata dari rentang timestamp ---
    days = (ts.iloc[-1] - ts.iloc[0]).days
    years = days / 365.25 if days > 0 else 0.0
    if years > 0 and initial_capital > 0 and end > 0:
        cagr = (end / initial_capital) ** (1.0 / years) - 1.0
    else:
        cagr = nan

    # --- Max drawdown (negatif, dalam persen) ---
    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    # drawdown bernilai 0 atau negatif; min() = paling negatif.
    max_dd_pct = float(drawdown.min()) * 100.0

    # --- Sharpe & Sortino dari daily return equity ---
    daily_returns = eq.pct_change().dropna()
    if len(daily_returns) == 0:
        sharpe = nan
        sortino = nan
    else:
        rf_daily = risk_free_rate / periods_per_year
        mean_r = float(daily_returns.mean())
        std_r = float(daily_returns.std())  # ddof=1 (sample std, default pandas)

        if std_r == 0 or pd.isna(std_r):
            sharpe = nan
        else:
            sharpe = (mean_r - rf_daily) / std_r * math.sqrt(periods_per_year)

        downside = daily_returns[daily_returns < 0]
        if len(downside) == 0:
            # Tidak ada return negatif sama sekali → tidak ada risiko bawah.
            sortino = float("inf") if (mean_r - rf_daily) > 0 else nan
        else:
            downside_std = float(downside.std())
            if downside_std == 0 or pd.isna(downside_std):
                sortino = nan
            else:
                sortino = (mean_r - rf_daily) / downside_std * math.sqrt(periods_per_year)

    # --- Calmar = CAGR / |max_drawdown| ---
    max_dd_decimal = abs(max_dd_pct / 100.0)
    if max_dd_decimal == 0:
        # Tidak pernah drawdown.
        calmar = float("inf") if (not pd.isna(cagr) and cagr > 0) else nan
    elif pd.isna(cagr):
        calmar = nan
    else:
        calmar = cagr / max_dd_decimal

    return {
        "total_return_pct": total_return_pct,
        "cagr": cagr,
        "max_drawdown_pct": max_dd_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
    }


# ---------------------------------------------------------------------------
# Fungsi utama
# ---------------------------------------------------------------------------

def compute_metrics(trades_df: pd.DataFrame, equity_df: pd.DataFrame,
                    initial_capital: float, periods_per_year: int = 365,
                    risk_free_rate: float = 0.0) -> dict:
    """Hitung metrik kinerja backtest.

    Parameter
    ---------
    trades_df : pd.DataFrame
        Output `run_backtest`, wajib punya kolom `net_pnl` dan
        `exit_reason`. Trade dengan exit_reason
        'rejected_insufficient_margin' diabaikan.
    equity_df : pd.DataFrame
        Output `run_backtest`, wajib punya kolom `timestamp` dan
        `equity`.
    initial_capital : float
        Modal awal untuk total_return_pct dan CAGR.
    periods_per_year : int, default 365
        Jumlah periode per tahun untuk annualisasi Sharpe/Sortino.
        Data harian crypto 24/7 → 365.
    risk_free_rate : float, default 0.0
        Risk-free rate tahunan (misal 0.05 = 5%). Dibagi
        periods_per_year untuk konversi ke per-periode.

    Return
    ------
    dict berisi 12 metrik:
        total_trades, win_rate, profit_factor, expectancy, avg_win,
        avg_loss, total_return_pct, cagr, max_drawdown_pct, sharpe,
        sortino, calmar.

    Pure function: tidak memodifikasi input.
    """
    # Filter trade rejected.
    if trades_df is None or len(trades_df) == 0:
        valid = trades_df.iloc[0:0] if trades_df is not None else pd.DataFrame(
            columns=["net_pnl", "exit_reason"]
        )
    else:
        valid = trades_df[trades_df["exit_reason"] != _REJECTED]

    result: dict = {}
    result.update(_trade_stats(valid))
    result.update(_equity_stats(equity_df, initial_capital,
                                 periods_per_year, risk_free_rate))
    return result
