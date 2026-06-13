"""backtest_engine.py — Simulator eksekusi bar-by-bar untuk strategi
trend-following daily (Bybit perpetual).

Mengonsumsi DataFrame sinyal (output `strategy_trend.compute_signals()`)
dan DataFrame funding harian, lalu menjalankan loop per bar yang
memutuskan: buka posisi, tutup di SL/TP, akumulasi funding, dan
menjaga ekuitas yang ter-compound.

Modul ini TIDAK menghitung metrik kinerja (CAGR, Sharpe, dst). Itu
tanggung jawab Modul 4.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helper kecil
# ---------------------------------------------------------------------------

def _build_funding_lookup(funding_df: pd.DataFrame) -> dict:
    """Bangun kamus {date -> funding_rate_daily_sum}. Toleran terhadap
    funding_df kosong atau None (kembalikan kamus kosong)."""
    if funding_df is None or len(funding_df) == 0:
        return {}
    dates = pd.to_datetime(funding_df["date"]).dt.date
    rates = funding_df["funding_rate_daily_sum"].astype(float)
    return dict(zip(dates, rates))


def _check_exit(side: str, sl: float, tp: float, bar_high: float,
                bar_low: float, tie_break: str
                ) -> tuple[float | None, str | None]:
    """Cek apakah bar menyentuh SL atau TP. Kembalikan (exit_price, reason)
    atau (None, None) jika tidak ada yang tersentuh."""
    if side == "long":
        hit_sl = bar_low <= sl
        hit_tp = bar_high >= tp
    else:
        hit_sl = bar_high >= sl
        hit_tp = bar_low <= tp
    if hit_sl and hit_tp:
        return (sl, "SL") if tie_break == "SL" else (tp, "TP")
    if hit_sl:
        return sl, "SL"
    if hit_tp:
        return tp, "TP"
    return None, None


def _close_position(position: dict, exit_price: float, exit_ts,
                    exit_reason: str, fee_taker: float,
                    slippage: float) -> dict:
    """Susun baris trade log untuk posisi yang ditutup. Kolom
    `equity_after` di-set NaN; pemanggil mengisinya setelah equity
    diperbarui."""
    side = position["side"]
    entry_price = position["entry_price_actual"]
    size_coin = position["size_coin"]

    notional_close = exit_price * size_coin
    exit_fee = notional_close * fee_taker
    exit_slip_cost = notional_close * slippage

    if side == "long":
        gross_pnl = (exit_price - entry_price) * size_coin
    else:
        gross_pnl = (entry_price - exit_price) * size_coin

    net_pnl = (gross_pnl
               - exit_fee - exit_slip_cost
               - position["entry_fee"] - position["entry_slippage_cost"]
               - position["funding_cost_accumulated"])

    return {
        "entry_time": position["entry_time"],
        "exit_time": exit_ts,
        "side": side,
        "entry_price_actual": entry_price,
        "exit_price_actual": exit_price,
        "sl_price": position["sl_price"],
        "tp_price": position["tp_price"],
        "size_coin": size_coin,
        "notional": position["notional"],
        "gross_pnl": gross_pnl,
        "fees_total": position["entry_fee"] + exit_fee,
        "slippage_total": position["entry_slippage_cost"] + exit_slip_cost,
        "funding_total": position["funding_cost_accumulated"],
        "net_pnl": net_pnl,
        "equity_after": np.nan,
        "exit_reason": exit_reason,
    }


_TRADE_LOG_COLUMNS = [
    "entry_time", "exit_time", "side", "entry_price_actual",
    "exit_price_actual", "sl_price", "tp_price", "size_coin",
    "notional", "gross_pnl", "fees_total", "slippage_total",
    "funding_total", "net_pnl", "equity_after", "exit_reason",
]
_EQUITY_CURVE_COLUMNS = ["timestamp", "equity", "open_position",
                        "unrealized_pnl"]


# ---------------------------------------------------------------------------
# Fungsi utama
# ---------------------------------------------------------------------------

def run_backtest(signals_df: pd.DataFrame, funding_df: pd.DataFrame,
                 initial_capital: float = 10000.0,
                 risk_per_trade: float = 0.03,
                 leverage: float = 10.0,
                 fee_taker: float = 0.00055,
                 slippage: float = 0.0005,
                 tie_break: str = "SL"
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Jalankan backtest bar-per-bar.

    Parameter
    ---------
    signals_df : pd.DataFrame
        Output `strategy_trend.compute_signals()`. Wajib berkolom
        `timestamp, open, high, low, close, signal, entry_price,
        sl_price, tp_price`, urut menaik berdasarkan timestamp, dan
        timestamp unik per baris.
    funding_df : pd.DataFrame
        Berkolom `date` dan `funding_rate_daily_sum`. Boleh kosong;
        tanggal yang tidak ditemukan dianggap funding = 0.
    initial_capital : float
        Modal awal dalam USD.
    risk_per_trade : float
        Fraksi ekuitas yang dirisikokan tiap trade (jarak ke SL).
    leverage : float
        Leverage notional untuk pengecekan margin (record-only).
    fee_taker : float
        Fee taker per sisi (default 0.055%).
    slippage : float
        Slippage adverse per sisi (default 0.05%).
    tie_break : {'SL', 'TP'}
        Sisi yang menang ketika SL & TP tersentuh di bar yang sama.
        Default 'SL' (worst-case, konservatif).

    Return
    ------
    (trade_log_df, equity_curve_df) : tuple of pd.DataFrame

        trade_log_df — satu baris per trade selesai (atau ditolak),
        kolom: entry_time, exit_time, side, entry_price_actual,
        exit_price_actual, sl_price, tp_price, size_coin, notional,
        gross_pnl, fees_total, slippage_total, funding_total, net_pnl,
        equity_after, exit_reason.
        exit_reason ∈ {'SL', 'TP', 'end_of_data',
                       'rejected_insufficient_margin'}.

        equity_curve_df — satu baris per bar input, kolom:
        timestamp, equity, open_position (bool), unrealized_pnl.

    Fungsi pure: input tidak dimodifikasi.
    """
    if tie_break not in ("SL", "TP"):
        raise ValueError("tie_break harus 'SL' atau 'TP'")

    funding_map = _build_funding_lookup(funding_df)

    equity = float(initial_capital)
    position: dict | None = None  # state posisi aktif
    pending: dict | None = None   # entry yang menunggu open bar berikut

    trade_log: list[dict] = []
    equity_curve: list[dict] = []

    for row in signals_df.itertuples(index=False):
        ts = row.timestamp
        bar_high = float(row.high)
        bar_low = float(row.low)
        bar_close = float(row.close)
        bar_date = pd.Timestamp(ts).date()

        # ---- 1. Eksekusi pending entry di harga open bar ini ----
        if pending is not None and position is None:
            side = pending["side"]
            entry_intended = pending["entry_price_intended"]
            sl = pending["sl_price"]
            tp = pending["tp_price"]

            # Slippage adverse pada sisi entry.
            entry_actual = (entry_intended * (1.0 + slippage)
                            if side == "long"
                            else entry_intended * (1.0 - slippage))

            stop_distance = abs(entry_actual - sl)
            risk_dollar = equity * risk_per_trade
            size_coin = ((risk_dollar / stop_distance)
                         if stop_distance > 0 else 0.0)
            notional = entry_actual * size_coin
            margin_required = (notional / leverage) if leverage > 0 else np.inf

            if (margin_required > equity or stop_distance <= 0
                    or size_coin <= 0 or np.isnan(margin_required)):
                # Tolak entry; catat ke trade log.
                trade_log.append({
                    "entry_time": ts, "exit_time": ts, "side": side,
                    "entry_price_actual": np.nan,
                    "exit_price_actual": np.nan,
                    "sl_price": sl, "tp_price": tp,
                    "size_coin": 0.0, "notional": notional,
                    "gross_pnl": 0.0, "fees_total": 0.0,
                    "slippage_total": 0.0, "funding_total": 0.0,
                    "net_pnl": 0.0, "equity_after": equity,
                    "exit_reason": "rejected_insufficient_margin",
                })
            else:
                entry_fee = notional * fee_taker
                entry_slip_cost = notional * slippage
                position = {
                    "entry_time": ts,
                    "side": side,
                    "entry_price_actual": entry_actual,
                    "sl_price": sl,
                    "tp_price": tp,
                    "size_coin": size_coin,
                    "notional": notional,
                    "entry_fee": entry_fee,
                    "entry_slippage_cost": entry_slip_cost,
                    "funding_cost_accumulated": 0.0,
                }
            pending = None

        # ---- 2. Cek SL/TP untuk posisi aktif ----
        if position is not None:
            exit_price, exit_reason = _check_exit(
                position["side"], position["sl_price"], position["tp_price"],
                bar_high, bar_low, tie_break,
            )
            if exit_reason is not None:
                trade_row = _close_position(
                    position, exit_price, ts, exit_reason,
                    fee_taker, slippage,
                )
                equity += trade_row["net_pnl"]
                trade_row["equity_after"] = equity
                trade_log.append(trade_row)
                position = None

        # ---- 3. Akumulasi funding jika posisi masih hidup ----
        if position is not None:
            rate = float(funding_map.get(bar_date, 0.0))
            sign = 1.0 if position["side"] == "long" else -1.0
            position["funding_cost_accumulated"] += (
                position["notional"] * rate * sign
            )

        # ---- 4. Sinyal baru -> simpan sebagai pending entry ----
        raw_sig = row.signal
        sig = 0 if pd.isna(raw_sig) else int(raw_sig)
        if position is None and pending is None and sig != 0:
            pending = {
                "side": "long" if sig == 1 else "short",
                "entry_price_intended": float(row.entry_price),
                "sl_price": float(row.sl_price),
                "tp_price": float(row.tp_price),
            }

        # ---- 5. Catat baris equity curve ----
        if position is not None:
            side = position["side"]
            entry_actual = position["entry_price_actual"]
            size_coin = position["size_coin"]
            unrealized = ((bar_close - entry_actual) * size_coin
                          if side == "long"
                          else (entry_actual - bar_close) * size_coin)
            equity_curve.append({
                "timestamp": ts, "equity": equity,
                "open_position": True, "unrealized_pnl": unrealized,
            })
        else:
            equity_curve.append({
                "timestamp": ts, "equity": equity,
                "open_position": False, "unrealized_pnl": np.nan,
            })

    # ---- Force close jika masih ada posisi terbuka di akhir data ----
    if position is not None and len(signals_df) > 0:
        last_row = signals_df.iloc[-1]
        exit_price = float(last_row["close"])
        exit_ts = last_row["timestamp"]
        trade_row = _close_position(
            position, exit_price, exit_ts, "end_of_data",
            fee_taker, slippage,
        )
        equity += trade_row["net_pnl"]
        trade_row["equity_after"] = equity
        trade_log.append(trade_row)
        # Perbarui baris equity curve terakhir supaya cerminkan post-close.
        equity_curve[-1] = {
            "timestamp": exit_ts, "equity": equity,
            "open_position": False, "unrealized_pnl": np.nan,
        }
        position = None

    trade_log_df = pd.DataFrame(trade_log, columns=_TRADE_LOG_COLUMNS)
    equity_curve_df = pd.DataFrame(equity_curve,
                                   columns=_EQUITY_CURVE_COLUMNS)
    return trade_log_df, equity_curve_df
