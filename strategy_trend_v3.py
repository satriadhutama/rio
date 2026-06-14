"""strategy_trend_v3.py — Versi v3 strategi trend-following.

Sama dengan v2 (ADX/EMA/MACD + filter funding & OI) ditambah satu
filter regime: **volatility per coin**.

Bar yang lolos v2 (`filter_reason == 'passed'`) diuji lagi terhadap
ambang volatility:
  - vol_ratio   = atr_14 / close
  - vol_p_lower = rolling quantile (default p10) dari vol_ratio
                  pada jendela `vol_lookback` hari, min_periods=60
  - vol_p_upper = rolling quantile (default p90), parameter sama

Bar diblok kalau vol_ratio jatuh di luar [p_lower, p_upper], atau
kalau quantile belum siap (warm-up). Filosofi:
  - vol_too_low  : pasar terlalu tenang -> sinyal kemungkinan whipsaw.
  - vol_too_high : sinyal lahir di puncak panik -> rawan reversal.

File v1 dan v2 tidak diubah; v3 = layer di atas v2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_trend_v2 import compute_signals_v2


def compute_signals_v3(
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
    vol_lower_percentile: float = 0.10,
    vol_upper_percentile: float = 0.90,
    vol_lookback: int = 365,
) -> pd.DataFrame:
    """Sinyal trend-following dengan filter funding + OI + volatility.

    Parameter
    ---------
    indicators_df, funding_df, oi_df, adx_threshold, atr_sl_mult,
    atr_tp_mult, funding_long_block, funding_short_block,
    oi_growth_min, oi_growth_window :
        Diteruskan ke `compute_signals_v2`.
    vol_lower_percentile : float, default 0.10
        Kuantil bawah dari rolling vol_ratio. Bar dengan
        vol_ratio < kuantil ini diblok dengan reason 'vol_too_low'.
    vol_upper_percentile : float, default 0.90
        Kuantil atas. vol_ratio > kuantil ini diblok dengan reason
        'vol_too_high'.
    vol_lookback : int, default 365
        Jendela rolling untuk menghitung kuantil. min_periods=60.

    Output (vs v2)
    --------------
    Tambah kolom:
        vol_ratio_at_signal : float, nilai vol_ratio di bar T;
                              NaN jika v1 signal == 0.
    filter_reason kemungkinan nilai baru:
        'vol_warmup'   — vol_ratio atau salah satu kuantil masih NaN.
        'vol_too_low'  — vol_ratio < vol_p_lower.
        'vol_too_high' — vol_ratio > vol_p_upper.

    Pure function: input tidak dimodifikasi.
    """
    # ---- 1. Jalankan v2 (yang internal sudah menjalankan v1) ----
    out = compute_signals_v2(
        indicators_df, funding_df, oi_df,
        adx_threshold=adx_threshold,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult,
        funding_long_block=funding_long_block,
        funding_short_block=funding_short_block,
        oi_growth_min=oi_growth_min,
        oi_growth_window=oi_growth_window,
    )

    # ---- 2. Hitung vol_ratio dan kuantil rolling ----
    close = out["close"].astype(float)
    atr = out["atr_14"].astype(float)
    vol_ratio = atr / close

    rolling = vol_ratio.rolling(window=vol_lookback, min_periods=60)
    vol_p_lower = rolling.quantile(vol_lower_percentile)
    vol_p_upper = rolling.quantile(vol_upper_percentile)

    # ---- 3. Bar eligible: yang lolos v2 ----
    # fillna("") menjamin tidak ada pd.NA yang menyusup ke boolean mask.
    eligible = out["filter_reason"].fillna("").eq("passed")

    # ---- 4. Mask filter ----
    # Warmup: salah satu dari vol_ratio / kuantil masih NaN.
    warmup_mask = eligible & (
        vol_p_lower.isna() | vol_p_upper.isna() | vol_ratio.isna()
    )
    # Numerik NaN dalam < / > otomatis False, tapi exclude warmup_mask
    # eksplisit demi kejelasan urutan prioritas.
    too_low_mask = eligible & ~warmup_mask & (vol_ratio < vol_p_lower)
    too_high_mask = eligible & ~warmup_mask & (vol_ratio > vol_p_upper)

    # ---- 5. Perbarui filter_reason ----
    reasons = out["filter_reason"].copy()
    reasons[warmup_mask] = "vol_warmup"
    reasons[too_low_mask] = "vol_too_low"
    reasons[too_high_mask] = "vol_too_high"
    out.loc[:, "filter_reason"] = reasons

    # ---- 6. Block sinyal yang gagal filter v3 ----
    block_mask = warmup_mask | too_low_mask | too_high_mask
    out.loc[block_mask, "signal"] = 0
    out.loc[block_mask, "entry_price"] = np.nan
    out.loc[block_mask, "sl_price"] = np.nan
    out.loc[block_mask, "tp_price"] = np.nan

    # ---- 7. Kolom diagnostik vol_ratio ----
    # Tampilkan vol_ratio untuk SETIAP bar yang v1 menghasilkan sinyal
    # (filter_reason != NA), terlepas dari diblok atau lolos.
    has_v1_signal = out["filter_reason"].notna()
    out.loc[:, "vol_ratio_at_signal"] = np.where(
        has_v1_signal, vol_ratio, np.nan,
    )

    return out
