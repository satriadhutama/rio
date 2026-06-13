"""indicators.py — Indikator teknikal berbasis library `ta`.

Semua fungsi bersifat pure: terima DataFrame, kembalikan DataFrame baru
dengan kolom indikator tambahan. Tidak ada I/O dan tidak ada print.
DataFrame masukan diharapkan punya kolom: timestamp, open, high, low,
close, volume (urut waktu menaik).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange


def add_ema(df: pd.DataFrame, periods: tuple[int, ...] = (50, 200)) -> pd.DataFrame:
    """Tambahkan kolom EMA (Exponential Moving Average) untuk tiap `periods`.

    Untuk default periods=(50, 200) menambah kolom `ema_50` dan `ema_200`.
    """
    out = df.copy()
    for period in periods:
        ema = EMAIndicator(close=out["close"], window=period, fillna=False)
        out.loc[:, f"ema_{period}"] = ema.ema_indicator()
    return out


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Tambahkan kolom ATR (Average True Range) bernama `atr_<period>`.

    Catatan: library `ta` mengabaikan `fillna=False` dan tetap mengisi
    periode warm-up dengan 0.0. Kita override `period` baris pertama
    menjadi NaN agar sesuai semantik matematis (ATR butuh `period` bar
    untuk smoothing pertama).
    """
    out = df.copy()
    atr = AverageTrueRange(
        high=out["high"], low=out["low"], close=out["close"],
        window=period, fillna=False,
    )
    col = f"atr_{period}"
    out.loc[:, col] = atr.average_true_range()
    out.iloc[:period, out.columns.get_loc(col)] = np.nan
    return out


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Tambahkan kolom ADX dan komponennya: `adx_<p>`, `di_plus_<p>`, `di_minus_<p>`.

    Catatan: library `ta` mengabaikan `fillna=False` dan tetap mengisi
    periode warm-up dengan 0.0. ADX butuh dua fase smoothing (DI lalu
    ADX itu sendiri), jadi `2 * period` baris pertama kita override
    menjadi NaN.
    """
    out = df.copy()
    adx = ADXIndicator(
        high=out["high"], low=out["low"], close=out["close"],
        window=period, fillna=False,
    )
    cols = (f"adx_{period}", f"di_plus_{period}", f"di_minus_{period}")
    out.loc[:, cols[0]] = adx.adx()
    out.loc[:, cols[1]] = adx.adx_pos()
    out.loc[:, cols[2]] = adx.adx_neg()
    warmup = 2 * period
    for c in cols:
        out.iloc[:warmup, out.columns.get_loc(c)] = np.nan
    return out


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
             signal: int = 9) -> pd.DataFrame:
    """Tambahkan kolom MACD: `macd_line`, `macd_signal`, `macd_hist`."""
    out = df.copy()
    macd = MACD(
        close=out["close"], window_slow=slow, window_fast=fast,
        window_sign=signal, fillna=False,
    )
    out.loc[:, "macd_line"] = macd.macd()
    out.loc[:, "macd_signal"] = macd.macd_signal()
    out.loc[:, "macd_hist"] = macd.macd_diff()
    return out


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung semua indikator (EMA, ATR, ADX, MACD) pada salinan DataFrame.

    DataFrame asli TIDAK dimodifikasi. Mengembalikan DataFrame baru yang
    berisi seluruh kolom asli ditambah kolom indikator.
    """
    out = df.copy()
    out = add_ema(out)
    out = add_atr(out)
    out = add_adx(out)
    out = add_macd(out)
    return out
