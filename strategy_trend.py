"""strategy_trend.py — Terjemahkan indikator menjadi sinyal entry mentah
untuk strategi trend-following daily (Bybit perpetual).

Modul ini PURE: hanya menghasilkan sinyal entry per bar. Ia TIDAK mengurus
posisi aktif, satu-posisi-per-coin, maupun logika exit — itu tanggung jawab
backtest engine (Modul 3).

Aturan strategi:
  1. Regime filter : adx_14 > threshold (default 20) → ada tren. Selain itu
     (termasuk NaN) bar dilewati.
  2. Bias arah     : ema_50 > ema_200 → 'long'; ema_50 < ema_200 → 'short';
     sama / salah satu NaN → NA.
  3. Entry trigger : MACD line memotong signal line (cross up / cross down).
  4. Long entry    : regime OK + bias 'long' + cross up.
  5. Short entry   : regime OK + bias 'short' + cross down.
  6. Eksekusi      : sinyal lahir di bar T (berdasar close), tetapi harga
     eksekusi diambil dari open bar T+1 (open.shift(-1)).
  7. SL/TP dihitung dari open[T+1] memakai atr_14 di bar T:
       Long : SL = open[T+1] - sl_mult*ATR ; TP = open[T+1] + tp_mult*ATR
       Short: SL = open[T+1] + sl_mult*ATR ; TP = open[T+1] - tp_mult*ATR
  8. Bar terakhir  : tidak punya T+1 → tidak boleh menghasilkan sinyal.

Input  : DataFrame hasil compute_all_indicators() dengan kolom timestamp,
         open, high, low, close, volume, ema_50, ema_200, atr_14, adx_14,
         di_plus_14, di_minus_14, macd_line, macd_signal, macd_hist.
Output : DataFrame baru (salinan) + kolom signal, entry_price, sl_price,
         tp_price, bias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_signals(df: pd.DataFrame, adx_threshold: float = 20.0,
                    atr_sl_mult: float = 2.0,
                    atr_tp_mult: float = 3.0) -> pd.DataFrame:
    """Hitung sinyal entry mentah dari DataFrame berindikator.

    Vectorized (tanpa loop bar-per-bar). Mengembalikan DataFrame baru;
    DataFrame masukan tidak dimodifikasi.
    """
    out = df.copy()

    # --- 1. Regime filter (NaN > threshold → False, otomatis terlewati) ---
    regime_ok = out["adx_14"] > adx_threshold

    # --- 2. Bias arah dari EMA (perbandingan dengan NaN → False) ---
    ema_long = out["ema_50"] > out["ema_200"]
    ema_short = out["ema_50"] < out["ema_200"]

    # --- 3. Cross detection MACD pakai shift() ---
    macd_prev = out["macd_line"].shift(1)
    signal_prev = out["macd_signal"].shift(1)
    cross_up = (macd_prev <= signal_prev) & (out["macd_line"] > out["macd_signal"])
    cross_down = (macd_prev >= signal_prev) & (out["macd_line"] < out["macd_signal"])

    # --- 6. Harga eksekusi = open bar T+1. Bar terakhir → NaN otomatis,
    #        sehingga next_open.notna() sekaligus menggugurkan sinyal di
    #        bar terakhir (syarat no. 8). ---
    next_open = out["open"].shift(-1)
    has_next = next_open.notna()

    # --- 4 & 5. Entry valid ---
    long_entry = regime_ok & ema_long & cross_up & has_next
    short_entry = regime_ok & ema_short & cross_down & has_next

    # --- signal: 1 / -1 / 0 ---
    signal = np.where(long_entry, 1, np.where(short_entry, -1, 0))
    out.loc[:, "signal"] = signal.astype(int)

    any_entry = long_entry | short_entry
    atr = out["atr_14"]

    # --- entry_price: open[T+1] jika ada sinyal, selain itu NaN ---
    out.loc[:, "entry_price"] = np.where(any_entry, next_open, np.nan)

    # --- 7. SL & TP dari open[T+1] memakai ATR bar T ---
    out.loc[:, "sl_price"] = np.where(
        long_entry, next_open - atr_sl_mult * atr,
        np.where(short_entry, next_open + atr_sl_mult * atr, np.nan),
    )
    out.loc[:, "tp_price"] = np.where(
        long_entry, next_open + atr_tp_mult * atr,
        np.where(short_entry, next_open - atr_tp_mult * atr, np.nan),
    )

    # --- bias: 'long' / 'short' / NA (object dtype) ---
    bias = pd.Series(pd.NA, index=out.index, dtype=object)
    bias[ema_long] = "long"
    bias[ema_short] = "short"
    out.loc[:, "bias"] = bias

    return out
