"""strategy_trend_v2.py — Versi v2 strategi trend-following.

Sama dengan v1 (`strategy_trend.compute_signals`) ditambah dua filter
regime berbasis data derivatif Bybit perpetual:

  1. **Funding rate ekstrem** -> block entry. LONG diblok kalau funding
     8h sangat positif (long bayar premi mahal ke short — kerumunan
     long over-leverage). SHORT diblok kalau funding sangat negatif
     (kerumunan short over-leverage).
  2. **Pertumbuhan Open Interest** -> block kalau OI hari ini <=
     rata-rata jendela rolling (`oi_growth_window`). Momentum hidup
     ditandai oleh OI yang naik melebihi recent baseline; OI yang
     datar/turun menandakan trend tanpa partisipasi baru.

File v1 (`strategy_trend.py`) sengaja TIDAK diubah agar bisa dipakai
sebagai baseline pembanding.

Output: DataFrame seperti hasil v1 ditambah 3 kolom diagnostik
(`funding_8h_at_signal`, `oi_growth_at_signal`, `filter_reason`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_trend import compute_signals


# ---------------------------------------------------------------------------
# Lookup builders
# ---------------------------------------------------------------------------

def _build_funding_map(funding_df: pd.DataFrame) -> dict:
    """Bangun {date -> funding_rate_8h_mean}. Toleran terhadap None/kosong."""
    if funding_df is None or len(funding_df) == 0:
        return {}
    dates = pd.to_datetime(funding_df["date"]).dt.date
    rates = funding_df["funding_rate_8h_mean"].astype(float)
    return dict(zip(dates, rates))


def _build_oi_growth_map(oi_df: pd.DataFrame, window: int) -> dict:
    """Bangun {date -> oi_growth} dengan oi_growth = OI / rolling_mean - 1.

    `window` hari pertama akan menghasilkan NaN (warm-up); di hilir
    NaN diperlakukan sebagai 'oi_missing' (filter gugur)."""
    if oi_df is None or len(oi_df) == 0:
        return {}
    df = oi_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    oi = df["open_interest_usd"].astype(float)
    rolling_mean = oi.rolling(window).mean()
    growth = oi / rolling_mean - 1.0
    return dict(zip(df["date"], growth))


# ---------------------------------------------------------------------------
# Fungsi utama
# ---------------------------------------------------------------------------

def compute_signals_v2(
    indicators_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    adx_threshold: float = 20.0,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    funding_long_block: float = 0.0003,
    funding_short_block: float = -0.00025,
    oi_growth_min: float = 0.0,
    oi_growth_window: int = 7,
) -> pd.DataFrame:
    """Sinyal trend-following + filter funding & OI growth.

    Parameter
    ---------
    indicators_df : pd.DataFrame
        Output `indicators.compute_all_indicators`.
    funding_df : pd.DataFrame
        Kolom `date` dan `funding_rate_8h_mean`. Tanggal tidak ditemukan
        -> diasumsikan funding = 0 (tidak akan memblok karena ekstrem).
    oi_df : pd.DataFrame
        Kolom `date` dan `open_interest_usd`. Akan dikonversi ke
        oi_growth = OI / rolling_mean(window) - 1.
    adx_threshold, atr_sl_mult, atr_tp_mult :
        Parameter v1 yang diteruskan ke `compute_signals`.
    funding_long_block : float, default 0.0003 (~p90 historis 0.03%/8h)
        Ambang funding 8h untuk memblokir LONG.
    funding_short_block : float, default -0.00025 (~p10 historis)
        Ambang funding 8h untuk memblokir SHORT.
    oi_growth_min : float, default 0.0
        Ambang minimum oi_growth supaya entry lolos. <= ambang -> blok.
    oi_growth_window : int, default 7
        Jendela rolling mean untuk OI growth.

    Return
    ------
    pd.DataFrame
        Semua kolom output v1, ditambah:
            funding_8h_at_signal : float, NaN jika v1 signal == 0.
            oi_growth_at_signal  : float, NaN jika v1 signal == 0
                                   atau jika OI hari itu missing.
            filter_reason        : str | pd.NA. Nilai:
                'passed'                — lolos v2.
                'funding_long_extreme'  — LONG diblok funding positif tinggi.
                'funding_short_extreme' — SHORT diblok funding negatif rendah.
                'oi_missing'            — OI growth NaN (data hilang / warm-up).
                'oi_flat'               — OI growth <= oi_growth_min.
                pd.NA                    — v1 sudah tidak menghasilkan signal.

    Pure function: input tidak dimodifikasi.
    """
    # ---- 1. Sinyal v1 (compute_signals sudah .copy() di dalam) ----
    out = compute_signals(
        indicators_df,
        adx_threshold=adx_threshold,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult,
    )

    # ---- 2. Lookup harian dari derivatif ----
    funding_map = _build_funding_map(funding_df)
    oi_growth_map = _build_oi_growth_map(oi_df, oi_growth_window)

    # ---- 3. Petakan tanggal bar -> funding & oi_growth ----
    bar_dates = pd.to_datetime(out["timestamp"]).dt.date
    funding_values = bar_dates.map(
        lambda d: funding_map.get(d, 0.0)
    ).astype(float)
    oi_values = bar_dates.map(
        lambda d: oi_growth_map.get(d, np.nan)
    ).astype(float)

    signal = out["signal"]
    has_signal = signal != 0
    is_long = signal == 1
    is_short = signal == -1

    # ---- 4. Susun filter_reason dengan urutan prioritas:
    #         funding -> OI missing -> OI flat.
    reasons = pd.Series(pd.NA, index=out.index, dtype=object)
    decided = pd.Series(False, index=out.index)

    # Default 'passed' untuk bar bersinyal — di-override jika kena filter.
    reasons[has_signal] = "passed"

    m = is_long & (funding_values > funding_long_block)
    reasons[m] = "funding_long_extreme"
    decided |= m

    m = is_short & (funding_values < funding_short_block)
    reasons[m] = "funding_short_extreme"
    decided |= m

    m = has_signal & ~decided & oi_values.isna()
    reasons[m] = "oi_missing"
    decided |= m

    m = (has_signal & ~decided & ~oi_values.isna()
         & (oi_values <= oi_growth_min))
    reasons[m] = "oi_flat"
    decided |= m

    # ---- 5. Block sinyal: set signal=0 dan entry/sl/tp=NaN ----
    block_mask = decided  # decided True hanya jika has_signal AND gagal filter
    out.loc[block_mask, "signal"] = 0
    out.loc[block_mask, "entry_price"] = np.nan
    out.loc[block_mask, "sl_price"] = np.nan
    out.loc[block_mask, "tp_price"] = np.nan

    # ---- 6. Tambah kolom diagnostik ----
    out.loc[:, "funding_8h_at_signal"] = np.where(
        has_signal, funding_values, np.nan,
    )
    out.loc[:, "oi_growth_at_signal"] = np.where(
        has_signal, oi_values, np.nan,
    )
    out.loc[:, "filter_reason"] = reasons

    return out
