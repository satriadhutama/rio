"""
monitor_idx.py — Screening Wyckoff/SMC bottom-up untuk top 100 saham IDX.

Tujuan: cari saham fase akhir AKUMULASI -> awal MARKUP untuk swing 1-10 hari.
Sistem ini MENYARING kandidat, BUKAN memutuskan. Verifikasi manual di RTI
(broker summary + foreign flow asli) tetap WAJIB sebelum entry.

================================================================
TRANSPARANSI DATA
  ASLI (Yahoo Finance)  : OHLC, Volume, Value (Volume x Close)
  PROXY (estimasi OHLCV): semua skor "Akumulasi/SmartMoney/ForeignFlow" di
                          bawah ini adalah ESTIMASI dari pola harga & volume,
                          BUKAN data broker summary / foreign flow asli.
  Setiap kolom proxy diberi label "(proxy)" di CSV & Telegram.

================================================================
ARSITEKTUR SKOR (semua 0-100 sebelum dibobot)

  Final = Akumulasi*0.40 + SmartMoney*0.25 + ForeignFlow*0.15
        + Volume*0.10    + Momentum*0.10   - RisikoDistribusi*0.20
  (di-clamp ke 0-100, lalu bonus/penalti tambahan di bawah, clamp lagi)

  1. Akumulasi (40%)        : ADL/OBV slope, range contraction, CLV, MFI, spring
  2. Smart Money (25%, proxy): volume climax, effort-vs-result, strong close, dst
  3. Foreign Flow (15%, PROXY): konsistensi, RS vs IHSG, money-flow streak
  4. Volume (10%)           : rasio vs avg5/avg20, tren, bonus likuiditas besar
  5. Momentum (10%)         : RSI/MACD/EMA, dengan penalti telat/overbought
  6. Risiko Distribusi (penalti 20%): upper-wick, lower-highs, divergensi ADL/MFI

  BONUS/PENALTI TAMBAHAN (langsung ke Final Score, setelah bobot di atas):
    + Fase Markup (Awal/Kuat) & RSI 45-65 & CLV10d>0.3 & volume naik
      5 hari berturut             -> +12 "Healthy markup with volume confirmation"
    + Fase Markup (Awal/Kuat) & ada shakeout 5 hari terakhir
                                   -> +8  "Shakeout absorbed, supply test passed"
    - Sudah naik >20% dlm 10 hari (overheat)               -> -10
    - RSI >78 (overbought ekstrem)                         -> -8
    - Upper wick >60% range, 3 hari berturut-turut         -> -5

  macro.txt TIDAK masuk Final Score -> hanya konteks di header Telegram.

================================================================
FASE WYCKOFF (tag per saham, lihat classify_phase())
  Akumulasi Awal / Akumulasi Matang / Markup Awal / Markup Kuat /
  Distribusi Awal / Distribusi Matang / Netral

KLASIFIKASI FINAL SCORE (kalibrasi audit/backtest -- distribusi skor IDX
realistis: skor maksimum sekitar 50, median sekitar 30)
  >=55 ⭐⭐⭐ SANGAT SIAP MARKUP   45-54 ⭐⭐ SIAP MARKUP
  35-44 ⭐ WATCHLIST PRIORITAS    25-34 ⚠️ PERLU KONFIRMASI
  <25  HINDARI (tidak dikirim Telegram)

================================================================
FILTER LIKUIDITAS (hard filter, sebelum skoring)
  Value 20d (Volume x Close rata-rata) < Rp 3 miliar -> SKIP
  Value 20d > Rp 20 miliar -> bonus +5 ke skor Volume

================================================================
FILE PENDUKUNG (edit manual, tanpa coding):
  top100.txt  : 100 ticker IDX (tanpa .JK)
  sektor.txt  : TICKER=SEKTOR
  macro.txt   : konteks makro (ditampilkan, tidak memengaruhi Final Score)

OUTPUT:
  ranking_lengkap.csv : semua saham (lolos filter) + skor tiap layer + alasan
  backtest_data.csv   : semua saham lolos ambang (Final_Score >= 35), utk audit
                        win-rate via update_backtest.py (return_5d/return_10d)
  gagal.txt           : ticker gagal diunduh
  Telegram            : maksimal 7 saham dengan Final Score >= 35, dikelompokkan
                        per label ⭐⭐⭐/⭐⭐/⭐

CARA JALANKAN:
  pip install yfinance pandas numpy requests
  export TELEGRAM_BOT_TOKEN="xxxx"
  export TELEGRAM_CHAT_ID="1674060319"
  python monitor_idx.py                 # full run + kirim Telegram
  python monitor_idx.py --no-telegram   # screening + CSV saja
  python monitor_idx.py --selftest      # uji logika offline (data sintetis)
  python update_backtest.py             # isi return 5d/10d & laporan win-rate
"""

import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Konfigurasi umum
# ---------------------------------------------------------------------------

DEFAULT_CHAT_ID = "1674060319"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

TOP100_FILE = "top100.txt"
SEKTOR_FILE = "sektor.txt"
MACRO_FILE = "macro.txt"
RANKING_CSV = "ranking_lengkap.csv"
BACKTEST_CSV = "backtest_data.csv"
GAGAL_FILE = "gagal.txt"
LOG_FILE = "trading_log.txt"

IHSG_TICKER = "^JKSE"

# Bobot Final Score (lihat docstring)
W_AKUMULASI, W_SMARTMONEY, W_FOREIGN = 0.40, 0.25, 0.15
W_VOLUME, W_MOMENTUM, W_RISIKO = 0.10, 0.10, 0.20

SCORE_THRESHOLD = 35                    # ambang minimal masuk Telegram
MAX_PICKS = 7

# Filter likuiditas
MIN_VALUE_20D = 3_000_000_000           # Rp 3 miliar -> hard filter
BONUS_VALUE_20D = 20_000_000_000        # Rp 20 miliar -> bonus skor volume

# Rate-limit Yahoo Finance
BATCH_SIZE = 10
BATCH_PAUSE = 2.5
MAX_RETRY = 3
HISTORY_PERIOD = "6mo"
MIN_BARS = 60

BIAS_VALUE = {"bullish": 100, "netral": 50, "bearish": 20}


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ===========================================================================
# INDIKATOR DASAR (manual, tanpa library 'ta')
# ===========================================================================

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def parabolic_sar(high: pd.Series, low: pd.Series,
                  af_step: float = 0.02, af_max: float = 0.2) -> pd.Series:
    n = len(high)
    sar = np.zeros(n)
    if n < 2:
        return pd.Series(sar, index=high.index)
    trend_up = True
    af = af_step
    ep = high.iloc[0]
    sar[0] = low.iloc[0]
    for i in range(1, n):
        sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
        if trend_up:
            sar[i] = min(sar[i], low.iloc[i - 1], low.iloc[max(0, i - 2)])
            if low.iloc[i] < sar[i]:
                trend_up, sar[i], ep, af = False, ep, low.iloc[i], af_step
            elif high.iloc[i] > ep:
                ep, af = high.iloc[i], min(af + af_step, af_max)
        else:
            sar[i] = max(sar[i], high.iloc[i - 1], high.iloc[max(0, i - 2)])
            if high.iloc[i] > sar[i]:
                trend_up, sar[i], ep, af = True, ep, high.iloc[i], af_step
            elif low.iloc[i] < ep:
                ep, af = low.iloc[i], min(af + af_step, af_max)
    return pd.Series(sar, index=high.index)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff().fillna(0)) * volume).cumsum()


def adl(high, low, close, volume) -> pd.Series:
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    return (mfm.fillna(0) * volume).cumsum()


def mfi(high, low, close, volume, period: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    raw = tp * volume
    pos = raw.where(tp > tp.shift(1), 0.0).rolling(period).sum()
    neg = raw.where(tp < tp.shift(1), 0.0).rolling(period).sum().replace(0, np.nan)
    return (100 - 100 / (1 + pos / neg)).fillna(50)


def atr(high, low, close, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def clv_series(high, low, close) -> pd.Series:
    """Close Location Value per-bar, range -1..+1 (+1 = close di high)."""
    rng = (high - low).replace(0, np.nan)
    return (((close - low) - (high - close)) / rng).fillna(0.0)


def swing_levels(high, low, price, left=2, right=2, lookback=60):
    """Support terdekat di bawah harga & resistance terdekat di atas harga."""
    h = high.tail(lookback).reset_index(drop=True)
    l = low.tail(lookback).reset_index(drop=True)
    piv_hi, piv_lo = [], []
    for i in range(left, len(h) - right):
        if h[i] == h[i - left:i + right + 1].max():
            piv_hi.append(h[i])
        if l[i] == l[i - left:i + right + 1].min():
            piv_lo.append(l[i])
    sup = [p for p in piv_lo if p < price]
    res = [p for p in piv_hi if p > price]
    support = max(sup) if sup else float(low.tail(lookback).min())
    resistance = min(res) if res else float(high.tail(lookback).max())
    return support, resistance


def slope01(series: pd.Series, window: int = 20) -> float:
    """Kemiringan series dinormalisasi ke 0..1 (0.5 = datar) via logistik."""
    if len(series) <= window:
        return 0.5
    recent = float(series.iloc[-1] - series.iloc[-1 - window])
    scale = float(series.diff().abs().tail(window).mean())
    if not scale or np.isnan(scale) or scale <= 1e-12:
        return 0.5
    z = recent / (scale * window)
    return float(1 / (1 + np.exp(-z)))


def pct_change_n(series: pd.Series, n: int) -> float:
    if len(series) <= n:
        return 0.0
    prev = float(series.iloc[-1 - n])
    if prev == 0:
        return 0.0
    return float(series.iloc[-1] / prev - 1)


def value_20d_rp(close: pd.Series, volume: pd.Series) -> float:
    return float((close * volume).rolling(20).mean().iloc[-1])


def compute_indicators(df: pd.DataFrame) -> dict:
    """Hitung sekali semua series dasar, dipakai bersama oleh semua skor."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    price = float(close.iloc[-1])
    _, _, hist = macd(close)
    support, resistance = swing_levels(high, low, price)
    return {
        "close": close, "high": high, "low": low, "volume": volume, "price": price,
        "ema20": ema(close, 20), "ema50": ema(close, 50),
        "rsi": rsi(close, 14), "macd_hist": hist,
        "sar": parabolic_sar(high, low),
        "obv": obv(close, volume), "adl": adl(high, low, close, volume),
        "mfi": mfi(high, low, close, volume), "atr": atr(high, low, close),
        "clv": clv_series(high, low, close),
        "avgvol20": volume.rolling(20).mean(),
        "avgrange20": (high - low).rolling(20).mean(),
        "support": support, "resistance": resistance,
    }


# ===========================================================================
# SKOR 1 — AKUMULASI (40%, proxy)
# ===========================================================================

def score_akumulasi(ind: dict) -> dict:
    close, volume, atr_s, clv_s, mfi_s = (ind["close"], ind["volume"],
                                          ind["atr"], ind["clv"], ind["mfi"])
    adl_s, obv_s = ind["adl"], ind["obv"]

    # (a) ADL slope 20 hari
    sub_adl = slope01(adl_s, 20) * 25

    # (b) OBV slope 20 hari + divergensi vs harga (OBV naik, harga sideways)
    obv_sl, price_sl = slope01(obv_s, 20), slope01(close, 20)
    sub_obv = obv_sl * 15
    divergence = obv_sl > 0.6 and abs(price_sl - 0.5) < 0.15
    if divergence:
        sub_obv += 10
    sub_obv = clamp(sub_obv, 0, 25)

    # (c) Range contraction (ATR menurun = absorpsi supply)
    atr_now = float(atr_s.iloc[-1])
    atr_prev = float(atr_s.iloc[-21]) if len(atr_s) > 21 else atr_now
    contraction_ratio = atr_now / atr_prev if atr_prev > 0 else 1.0
    range_contraction = contraction_ratio < 0.85
    sub_range = clamp((1 - contraction_ratio) / 0.5 * 15, 0, 15)

    # (d) CLV rata-rata 10 hari (>0.3 = serapan beli konsisten)
    clv_avg10 = float(clv_s.tail(10).mean())
    sub_clv = clamp((clv_avg10 + 1) / 2 * 15, 0, 15)

    # (e) MFI 14 stabil di area 40-60 (dana masuk tanpa euforia)
    mfi_recent = mfi_s.tail(10)
    frac_stable = float(((mfi_recent >= 40) & (mfi_recent <= 60)).mean())
    sub_mfi = frac_stable * 10

    # (f) Lower-wick frequency 20 hari: close >60% dari range (clv > 0.2)
    #     -> proxy shakeout & rebound (spring)
    spring_freq = float((clv_s.tail(20) > 0.2).mean())
    sub_spring = spring_freq * 10

    score = sub_adl + sub_obv + sub_range + sub_clv + sub_mfi + sub_spring

    # --- Bonus pola "akumulasi matang" ---
    notes = []
    rng20 = float(close.tail(20).max() - close.tail(20).min())
    sideways_rising_vol = (rng20 / close.iloc[-1] < 0.15) and slope01(volume, 10) > 0.6
    if sideways_rising_vol:
        score += 4
        notes.append("sideways 20 hari + volume naik bertahap")

    # shakeout = hari bikin low 20-hari baru tapi close rebound ke atas open proxy
    low20 = ind["low"].tail(20)
    rolling_min = low20.cummin().shift(1)
    open_proxy = (ind["high"] + ind["low"]) / 2
    shakeout_count = int(((low20 < rolling_min) & (close.tail(20) > open_proxy.tail(20))).sum())
    if 1 <= shakeout_count <= 2:
        score += 3
        notes.append(f"{shakeout_count} hari shakeout (low baru, close rebound)")

    # shakeout dalam 5 hari terakhir (rolling-20 min) -> dipakai utk bonus Markup Kuat
    rolling_min20 = ind["low"].rolling(20).min().shift(1)
    shakeout_mask = (ind["low"] < rolling_min20) & (close > open_proxy)
    shakeout_5d = bool(shakeout_mask.tail(5).any())

    mfi_bull_div = False
    if len(close) > 16:
        win_close, win_mfi = close.tail(15), mfi_s.tail(15)
        idx_min = win_close.idxmin()
        if idx_min != win_close.index[-1]:
            price_ll = close.iloc[-1] <= float(win_close.loc[idx_min]) * 1.01
            mfi_higher = float(mfi_s.iloc[-1]) > float(win_mfi.loc[idx_min])
            mfi_bull_div = price_ll and mfi_higher
    if mfi_bull_div:
        score += 3
        notes.append("divergensi bullish MFI vs harga")

    score = clamp(score, 0, 100)
    if range_contraction:
        notes.insert(0, f"range contraction (ATR {contraction_ratio:.2f}x)")
    if clv_avg10 > 0.3:
        notes.insert(0, f"CLV rata-rata 10d {clv_avg10:.2f} (serapan beli)")

    return {
        "skor_akumulasi": round(score, 1),
        "_range_contraction": range_contraction,
        "_sideways_rising_vol": sideways_rising_vol,
        "_shakeout_count": shakeout_count,
        "_shakeout_5d": shakeout_5d,
        "_clv_avg10": clv_avg10,
        "_mfi_bull_div": mfi_bull_div,
        "_notes_akumulasi": notes,
    }


# ===========================================================================
# SKOR 2 — SMART MONEY (25%, proxy OHLCV)
# ===========================================================================

def score_smart_money(ind: dict) -> dict:
    close, high, low, volume = ind["close"], ind["high"], ind["low"], ind["volume"]
    avgvol20, avgrange20, clv_s, atr_s = (ind["avgvol20"], ind["avgrange20"],
                                          ind["clv"], ind["atr"])
    notes = []

    # (a) Volume climax di area low: vol >3x avg20 & low dekat rolling-min
    climax_count = 0
    tail_n = min(15, len(close))
    roll_low20 = low.rolling(20).min()
    for i in range(len(close) - tail_n, len(close)):
        if i < 20:
            continue
        if volume.iloc[i] > 3 * avgvol20.iloc[i] and low.iloc[i] <= roll_low20.iloc[i] * 1.02:
            climax_count += 1
    sub_climax = clamp(climax_count, 0, 2) / 2 * 20
    if climax_count:
        notes.append(f"{climax_count} hari volume climax dekat area low")

    # (b) Effort vs result: volume besar tapi range kecil = absorpsi
    absorption_days = 0
    for i in range(len(close) - 10, len(close)):
        if i < 20:
            continue
        if volume.iloc[i] > 1.5 * avgvol20.iloc[i] and (high.iloc[i] - low.iloc[i]) < 0.7 * avgrange20.iloc[i]:
            absorption_days += 1
    sub_effort = clamp(absorption_days, 0, 3) / 3 * 20
    if absorption_days:
        notes.append(f"{absorption_days} hari absorpsi (volume besar, range kecil)")

    # (c) Strong close: close di 30% atas range (clv > 0.4), >5 dari 10 hari
    strong_count = int((clv_s.tail(10) > 0.4).sum())
    sub_strongclose = clamp(strong_count / 10, 0, 1) * 20
    if strong_count > 5:
        notes.append(f"strong close {strong_count}/10 hari")

    # (d) No supply test: ATR & volume sama-sama menurun 5 hari terakhir
    atr_down = len(atr_s) > 6 and float(atr_s.iloc[-1]) < float(atr_s.iloc[-6])
    vol_down = volume.tail(5).mean() < volume.tail(10).mean()
    sub_nosupply = 15 if (atr_down and vol_down) else (7 if (atr_down or vol_down) else 0)
    if atr_down and vol_down:
        notes.append("no-supply: ATR & volume menurun")

    # (e) Sign of strength: hari naik, volume > avg20, close kuat
    up_today = bool(close.iloc[-1] > close.iloc[-2])
    vol_above = bool(volume.iloc[-1] > avgvol20.iloc[-1])
    strong_today = bool(clv_s.iloc[-1] > 0.4)
    n_true = sum([up_today, vol_above, strong_today])
    sub_sos = 15 if n_true == 3 else (8 if n_true == 2 else 0)
    if n_true == 3:
        notes.append("sign of strength hari ini")

    # (f) Down-day volume drying: volume turun lebih kecil di hari merah
    ret = close.diff()
    down_vol = volume[ret < 0].tail(10)
    up_vol = volume[ret > 0].tail(10)
    sub_dryup = 0.0
    if len(down_vol) and len(up_vol):
        ratio = float(down_vol.mean() / up_vol.mean())
        if ratio < 1:
            sub_dryup = clamp(1 - ratio, 0, 1) * 10
            if sub_dryup > 5:
                notes.append("volume hari merah mengecil (supply mengering)")

    score = clamp(sub_climax + sub_effort + sub_strongclose + sub_nosupply
                  + sub_sos + sub_dryup, 0, 100)

    return {"skor_smart_money": round(score, 1), "_notes_smart_money": notes}


# ===========================================================================
# SKOR 3 — FOREIGN FLOW (15%, PROXY — bukan data asing asli, verifikasi RTI)
# ===========================================================================

def score_foreign_flow(ind: dict, ihsg_close: pd.Series | None) -> dict:
    close, volume = ind["close"], ind["volume"]
    notes = []

    # (a) Volume sesi penutupan (proxy): candle terakhir vs avg5 sebelumnya
    avg5_prev = float(volume.rolling(5).mean().iloc[-2]) if len(volume) > 6 else float(volume.mean())
    ratio_last = float(volume.iloc[-1] / avg5_prev) if avg5_prev > 0 else 1.0
    up_today = bool(close.iloc[-1] > close.iloc[-2])
    if ratio_last > 1.3 and up_today:
        sub_close = 25.0
        notes.append("volume closing besar + harga naik")
    elif ratio_last > 1.3:
        sub_close = 15.0
    elif ratio_last > 1.0:
        sub_close = 10.0
    else:
        sub_close = 5.0

    # (b) Konsistensi green close + volume meningkat, 5 hari terakhir
    ret = close.diff()
    cnt = 0
    for i in range(len(close) - 5, len(close)):
        if i < 1:
            continue
        if ret.iloc[i] > 0 and volume.iloc[i] > volume.iloc[i - 1]:
            cnt += 1
    sub_consistency = cnt / 5 * 25
    if cnt >= 4:
        notes.append(f"{cnt}/5 hari green close + volume naik")

    # (c) Relative strength vs IHSG 10 hari
    if ihsg_close is not None and len(ihsg_close) > 11:
        rs = pct_change_n(close, 10) - pct_change_n(ihsg_close, 10)
        sub_rs = 30 / (1 + np.exp(-rs / 0.03))
        if rs > 0.02:
            notes.append(f"outperform IHSG {rs*100:+.1f}% (10d)")
        elif rs < -0.02:
            notes.append(f"underperform IHSG {rs*100:+.1f}% (10d)")
    else:
        sub_rs = 15.0  # netral, data IHSG tidak tersedia

    # (d) Money flow positif (typical price naik) dalam 10 hari
    tp = (ind["high"] + ind["low"] + close) / 3
    mf_pos = (tp.diff() > 0).tail(10)
    cnt_pos = int(mf_pos.sum())
    sub_mf = cnt_pos / 10 * 20
    if cnt_pos == 10:
        notes.append("10 hari money-flow positif berturut-turut")

    score = clamp(sub_close + sub_consistency + sub_rs + sub_mf, 0, 100)
    return {"skor_foreign_flow": round(score, 1), "_notes_foreign": notes}


# ===========================================================================
# SKOR 4 — VOLUME (10%)
# ===========================================================================

def score_volume(ind: dict, value_20d: float) -> dict:
    close, volume, avgvol20 = ind["close"], ind["volume"], ind["avgvol20"]
    notes = []
    up_today = bool(close.iloc[-1] > close.iloc[-2])

    # (a) volume hari ini vs avg5 sebelumnya
    avg5_prev = float(volume.rolling(5).mean().iloc[-2]) if len(volume) > 6 else float(volume.mean())
    ratio5 = float(volume.iloc[-1] / avg5_prev) if avg5_prev > 0 else 1.0
    sub_a = 20 if ratio5 >= 1.5 else (10 if ratio5 >= 1.0 else 5)

    # (b) volume hari ini vs avg20
    ratio20 = float(volume.iloc[-1] / avgvol20.iloc[-1]) if avgvol20.iloc[-1] > 0 else 1.0
    if ratio20 >= 2:
        sub_b = 25
    elif ratio20 >= 1.5:
        sub_b = 15
    elif ratio20 >= 1:
        sub_b = 8
    else:
        sub_b = 3
    if ratio20 >= 1.5:
        notes.append(f"volume {ratio20:.1f}x avg20")

    # (c) tren volume naik 5-10 hari
    sub_c = slope01(volume, 10) * 20

    # (d) volume climax >4x avg -> bisa exhaustion
    climax = ratio20 >= 4
    if climax:
        sub_d = 10 if up_today else 4
        notes.append("volume climax >4x avg - waspada exhaustion")
    else:
        sub_d = 8

    # (e) volume naik di hari naik (10 hari terakhir)
    ret = close.diff()
    up_days = ret.tail(10) > 0
    if up_days.any():
        sub_e = float((volume.tail(10)[up_days] > avgvol20.tail(10)[up_days]).mean()) * 15
    else:
        sub_e = 0.0

    # (f) volume turun di hari turun (supply mengering)
    down_days = ret.tail(10) < 0
    if down_days.any():
        sub_f = float((volume.tail(10)[down_days] < avgvol20.tail(10)[down_days]).mean()) * 10
    else:
        sub_f = 5.0

    score = sub_a + sub_b + sub_c + sub_d + sub_e + sub_f

    # Bonus likuiditas besar
    if value_20d > BONUS_VALUE_20D:
        score += 5
        notes.append(f"likuiditas besar (value20d Rp{value_20d/1e9:.1f}M, bonus +5)")

    return {"skor_volume": round(clamp(score, 0, 100), 1), "_notes_volume": notes}


# ===========================================================================
# SKOR 5 — MOMENTUM (10%), fokus swing 1-10 hari
# ===========================================================================

def score_momentum(ind: dict) -> dict:
    close, high = ind["close"], ind["high"]
    price = ind["price"]
    ema20, ema50 = ind["ema20"], ind["ema50"]
    rsi_s, hist = ind["rsi"], ind["macd_hist"]
    notes = []

    rsi_now, rsi_prev = float(rsi_s.iloc[-1]), float(rsi_s.iloc[-2])
    ema20_now, ema50_now = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    hist_now, hist_prev = float(hist.iloc[-1]), float(hist.iloc[-2])

    # (a) RSI 45-65 dan naik
    if 45 <= rsi_now <= 65 and rsi_now > rsi_prev:
        sub_a = 20
        notes.append(f"RSI {rsi_now:.0f} di zona sehat & naik")
    elif 45 <= rsi_now <= 65:
        sub_a = 12
    elif rsi_now < 30:
        sub_a = 8
    else:
        sub_a = 4

    # (b) MACD histogram positif & membesar
    if hist_now > 0 and hist_now > hist_prev:
        sub_b = 20
        notes.append("MACD histogram positif & membesar")
    elif hist_now > 0:
        sub_b = 12
    elif hist_now > hist_prev:
        sub_b = 8
    else:
        sub_b = 0

    # (c) harga di atas EMA20 & EMA20 di atas EMA50
    above20, above50 = price > ema20_now, ema20_now > ema50_now
    if above20 and above50:
        sub_c = 20
        notes.append("harga > EMA20 > EMA50")
    elif above20:
        sub_c = 10
    else:
        sub_c = 0

    # (d) slope EMA20 5 hari positif
    ema20_slope_pos = len(ema20) > 6 and ema20.iloc[-1] > ema20.iloc[-6]
    sub_d = 15 if ema20_slope_pos else 0

    # (e) posisi harga terhadap range 20 hari (atas 50% lebih baik)
    low20, high20 = ind["low"].tail(20).min(), high.tail(20).max()
    pos20 = (price - low20) / (high20 - low20) if high20 > low20 else 0.5
    sub_e = clamp(pos20, 0, 1) * 25

    score = sub_a + sub_b + sub_c + sub_d + sub_e

    # --- Penalti: telat / overbought / mentok resistance ---
    if rsi_now > 75:
        score -= 15
        notes.append("RSI >75 overbought - berisiko telat")

    run10 = pct_change_n(close, 10)
    if run10 > 0.25:
        score -= 15
        notes.append(f"harga sudah naik {run10*100:.0f}% dlm 10 hari - sudah lari")

    if len(high) >= 60:
        high60 = float(high.tail(60).max())
        if high60 * 0.98 <= price < high60:
            score -= 10
            notes.append("mentok resistance jangka panjang, belum breakout")

    return {
        "skor_momentum": round(clamp(score, 0, 100), 1),
        "_above_ema20": above20, "_above_ema50_chain": above20 and above50,
        "_ema20_above_ema50": above50,
        "_ema20_slope_pos": ema20_slope_pos,
        "_notes_momentum": notes,
    }


# ===========================================================================
# SKOR 6 — RISIKO DISTRIBUSI (penalti 20%)
# ===========================================================================

def score_risiko_distribusi(ind: dict) -> dict:
    close, high, volume = ind["close"], ind["high"], ind["volume"]
    clv_s, mfi_s, adl_s, avgvol20 = ind["clv"], ind["mfi"], ind["adl"], ind["avgvol20"]
    notes = []

    # (a) Upper-wick frequency 10 hari (close jauh di bawah high, clv < -0.2)
    sub_a = float((clv_s.tail(10) < -0.2).mean()) * 20
    if sub_a > 10:
        notes.append("upper-wick sering muncul (supply di atas)")

    # (b) Volume naik saat harga turun (10 hari)
    ret = close.diff()
    down_days = ret.tail(10) < 0
    if down_days.any():
        sub_b = float((volume.tail(10)[down_days] > avgvol20.tail(10)[down_days]).mean()) * 20
    else:
        sub_b = 0.0
    if sub_b > 10:
        notes.append("volume naik di hari turun (distribusi)")

    # (c) Lower highs 10 hari terakhir (paruh kedua < paruh pertama)
    h10 = high.tail(10)
    prior_high = float(h10.head(5).max())
    recent_high = float(h10.tail(5).max())
    lower_highs = recent_high < prior_high
    sub_c = 20 if lower_highs else 0
    if lower_highs:
        notes.append("lower highs 10 hari terakhir")

    # (d) MFI overbought >80 dlm 10 hari lalu turun <70 sekarang
    was_ob = bool((mfi_s.tail(10) > 80).any())
    now_below = float(mfi_s.iloc[-1]) < 70
    sub_d = 20 if (was_ob and now_below) else 0
    if sub_d:
        notes.append("MFI turun dari overbought (>80 -> <70)")

    # (e) ADL turun saat harga naik (bearish divergence)
    price_sl, adl_sl = slope01(close, 10), slope01(adl_s, 10)
    bearish_div = price_sl > 0.6 and adl_sl < 0.4
    sub_e = 20 if bearish_div else 0
    if bearish_div:
        notes.append("ADL turun saat harga naik (divergensi bearish)")

    score = clamp(sub_a + sub_b + sub_c + sub_d + sub_e, 0, 100)
    return {
        "skor_risiko_distribusi": round(score, 1),
        "_lower_highs": lower_highs,
        "_notes_risiko": notes,
    }


# ===========================================================================
# KLASIFIKASI FASE WYCKOFF & FINAL SCORE
# ===========================================================================

def classify_phase(akum, vol, mom, risiko, rsi_now, range_contraction,
                   lower_highs, above_ema20) -> str:
    # Distribusi diprioritaskan (peringatan)
    if risiko > 70 and lower_highs:
        return "Distribusi Matang"
    if risiko > 50 and rsi_now > 70:
        return "Distribusi Awal"
    if mom > 70 and above_ema20:
        return "Markup Kuat"
    if akum > 65 and 50 <= mom <= 70 and above_ema20:
        return "Markup Awal"
    if akum > 70 and vol >= 50 and range_contraction:
        return "Akumulasi Matang"
    if 50 <= akum <= 70 and vol < 50 and mom < 40:
        return "Akumulasi Awal"
    return "Netral"


def classify_score(final: float) -> tuple:
    """Return (label, bintang) berdasarkan Final Score."""
    if final >= 55:
        return "SANGAT SIAP MARKUP", "⭐⭐⭐"
    if final >= 45:
        return "SIAP MARKUP", "⭐⭐"
    if final >= 35:
        return "WATCHLIST PRIORITAS", "⭐"
    if final >= 25:
        return "PERLU KONFIRMASI", "⚠️"
    return "HINDARI", ""


def build_alasan(phase, a, sm, ff, vol, mom, risiko, adj_notes=None) -> str:
    """Gabung catatan tiap layer jadi satu kalimat audit singkat."""
    bits = []
    if a["_notes_akumulasi"]:
        bits.append("; ".join(a["_notes_akumulasi"][:3]))
    if sm["_notes_smart_money"]:
        bits.append("; ".join(sm["_notes_smart_money"][:2]))
    if mom["_notes_momentum"]:
        bits.append("; ".join(mom["_notes_momentum"][:2]))
    if risiko["_notes_risiko"]:
        bits.append("Risiko: " + "; ".join(risiko["_notes_risiko"][:2]))
    foreign = "Foreign proxy " + ("bullish" if ff["skor_foreign_flow"] >= 55
                                  else "netral/lemah") + " (verifikasi RTI)"
    bits.append(foreign)
    if adj_notes:
        bits.append("; ".join(adj_notes))
    text = f"[{phase}] " + ". ".join(b for b in bits if b)
    return text[:300]


def score_stock(df: pd.DataFrame, sektor: str, ihsg_close, value_20d: float) -> dict:
    ind = compute_indicators(df)

    a = score_akumulasi(ind)
    sm = score_smart_money(ind)
    ff = score_foreign_flow(ind, ihsg_close)
    vol = score_volume(ind, value_20d)
    mom = score_momentum(ind)
    risiko = score_risiko_distribusi(ind)

    final = (a["skor_akumulasi"] * W_AKUMULASI
             + sm["skor_smart_money"] * W_SMARTMONEY
             + ff["skor_foreign_flow"] * W_FOREIGN
             + vol["skor_volume"] * W_VOLUME
             + mom["skor_momentum"] * W_MOMENTUM
             - risiko["skor_risiko_distribusi"] * W_RISIKO)
    final = clamp(final, 0, 100)

    rsi_now = float(ind["rsi"].iloc[-1])
    phase = classify_phase(a["skor_akumulasi"], vol["skor_volume"],
                           mom["skor_momentum"], risiko["skor_risiko_distribusi"],
                           rsi_now, a["_range_contraction"],
                           risiko["_lower_highs"], mom["_above_ema20"])

    # --- Bonus/penalti tambahan langsung ke Final Score (lihat docstring) ---
    adj_notes = []
    run10 = pct_change_n(ind["close"], 10)
    clv_avg10 = a["_clv_avg10"]
    vol_avg_recent5 = float(ind["volume"].tail(5).mean())
    vol_avg_prior5 = float(ind["volume"].iloc[-10:-5].mean())
    volume_naik_5d = vol_avg_recent5 > vol_avg_prior5

    if phase in ("Markup Awal", "Markup Kuat"):
        if 45 <= rsi_now <= 65 and clv_avg10 > 0.3 and volume_naik_5d:
            final += 12
            adj_notes.append("+12 bonus: Healthy markup with volume confirmation")
        if a["_shakeout_5d"]:
            final += 8
            adj_notes.append("+8 bonus: Shakeout absorbed, supply test passed")

    if run10 > 0.20:
        final -= 10
        adj_notes.append(f"-10 penalti: harga sudah naik {run10*100:.0f}% dlm 10 hari (overheat)")

    if rsi_now > 78:
        final -= 8
        adj_notes.append("-8 penalti: RSI>78 (overbought ekstrem)")

    clv_last3 = ind["clv"].tail(3)
    if len(clv_last3) == 3 and bool((clv_last3 < -0.2).all()):
        final -= 5
        adj_notes.append("-5 penalti: upper wick >60% range 3 hari berturut (supply kuat di atas)")

    final = clamp(final, 0, 100)
    label, bintang = classify_score(final)

    price = ind["price"]
    entry = round(price, 2)
    sl = round(entry * 0.97, 2)
    tp = ind["resistance"] if ind["resistance"] > entry else round(entry * 1.05, 2)
    change_pct = round(pct_change_n(ind["close"], 1) * 100, 2)
    change5d_pct = round(pct_change_n(ind["close"], 5) * 100, 2)
    alasan = build_alasan(phase, a, sm, ff, vol, mom, risiko, adj_notes)

    return {
        "Sektor": sektor, "Harga": entry, "Change%": change_pct,
        "Change5d%": change5d_pct, "RSI": round(rsi_now, 1),
        "Value_20d_Rp": round(value_20d),
        "Skor_Akumulasi": a["skor_akumulasi"],
        "Skor_SmartMoney": sm["skor_smart_money"],
        "Skor_ForeignFlow_PROXY": ff["skor_foreign_flow"],
        "Skor_Volume": vol["skor_volume"],
        "Skor_Momentum": mom["skor_momentum"],
        "RisikoDistribusi": risiko["skor_risiko_distribusi"],
        "Final_Score": round(final, 1),
        "Klasifikasi": label, "Bintang": bintang,
        "Fase_Wyckoff": phase,
        "Entry": entry, "SL_3pct": sl, "TP_resistance": tp,
        "Alasan": alasan,
    }


# ===========================================================================
# FILE KONFIGURASI
# ===========================================================================

def read_lines(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]


def load_top100() -> list:
    codes = [c.upper().replace(".JK", "") for c in read_lines(TOP100_FILE)]
    if not codes:
        print(f"[WARN] {TOP100_FILE} kosong/tidak ada.")
    return codes


def load_sektor() -> dict:
    out = {}
    for line in read_lines(SEKTOR_FILE):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().upper()] = v.strip().upper()
    return out


def load_macro() -> dict:
    out = {}
    for line in read_lines(MACRO_FILE):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().upper()] = v.strip().lower()
    return out


def macro_context_line(macro: dict) -> str:
    if not macro:
        return "🌐 Makro: (macro.txt kosong) — konteks saja, tidak memengaruhi skor"
    ihsg = macro.get("IHSG_BIAS", "netral")
    usd = macro.get("USD_IDR", "netral")
    kom = macro.get("KOMODITAS", "netral")
    risk = macro.get("RISK_SENTIMENT", "netral")
    return (f"🌐 Konteks makro (info saja): IHSG {ihsg} | USD/IDR {usd} | "
            f"Komoditas {kom} | Risk {risk}")


# ===========================================================================
# DOWNLOAD OHLCV (batch + retry + rate-limit)
# ===========================================================================

EXPECTED_OHLCV = {"Open", "High", "Low", "Close", "Volume"}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns and fix lowercase from newer yfinance."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    rename_map = {}
    for col in df.columns:
        if isinstance(col, str) and col.capitalize() in EXPECTED_OHLCV and col != col.capitalize():
            rename_map[col] = col.capitalize()
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def fetch_batch(codes: list, period: str = HISTORY_PERIOD) -> dict:
    import yfinance as yf
    tickers = [f"{c}.JK" for c in codes]
    data = yf.download(tickers, period=period, interval="1d", progress=False,
                       auto_adjust=True, group_by="ticker", threads=True)
    out = {}
    for c in codes:
        t = f"{c}.JK"
        try:
            sub = data if len(tickers) == 1 else data[t]
            sub = normalize_columns(sub)
            if "Close" not in sub.columns:
                print(f"  {c}: kolom 'Close' tidak ditemukan, skip")
                continue
            sub = sub.dropna()
            if len(sub) >= MIN_BARS:
                out[c] = sub
        except Exception:                                # noqa: BLE001
            continue
    return out


def chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def download_all(codes: list) -> tuple:
    data_map, gagal = {}, []
    batches = list(chunks(codes, BATCH_SIZE))
    for bi, batch in enumerate(batches, 1):
        remaining = list(batch)
        for attempt in range(1, MAX_RETRY + 1):
            try:
                got = fetch_batch(remaining)
            except Exception as exc:                     # noqa: BLE001
                print(f"  batch {bi} attempt {attempt} error: {exc}")
                got = {}
            data_map.update(got)
            remaining = [c for c in remaining if c not in data_map]
            if not remaining:
                break
            if attempt < MAX_RETRY:
                wait = BATCH_PAUSE * attempt
                print(f"  batch {bi}: {len(remaining)} gagal, retry {attempt+1} dalam {wait:.0f}s")
                time.sleep(wait)
        if remaining:
            gagal.extend(remaining)
        ok = len(batch) - len([c for c in batch if c in gagal])
        print(f"[batch {bi}/{len(batches)}] ok={ok} gagal={[c for c in batch if c in gagal]}")
        time.sleep(BATCH_PAUSE)
    return data_map, gagal


def fetch_ihsg():
    try:
        import yfinance as yf
        df = yf.download(IHSG_TICKER, period="3mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = normalize_columns(df)
        if "Close" not in df.columns:
            print("[WARN] IHSG: kolom 'Close' tidak ditemukan")
            return None
        return df["Close"].astype(float)
    except Exception as exc:                             # noqa: BLE001
        print(f"[WARN] gagal ambil IHSG (RS pakai netral): {exc}")
        return None


# ===========================================================================
# OUTPUT
# ===========================================================================

def fmt(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:.2f}"


CSV_COLUMNS = [
    "Tanggal", "Jam", "Ticker", "Sektor", "Harga", "Change%", "Change5d%", "RSI", "Value_20d_Rp",
    "Skor_Akumulasi", "Skor_SmartMoney", "Skor_ForeignFlow_PROXY",
    "Skor_Volume", "Skor_Momentum", "RisikoDistribusi",
    "Final_Score", "Klasifikasi", "Fase_Wyckoff",
    "Entry", "SL_3pct", "TP_resistance", "Alasan",
]


def save_csv(rows: list, path: str = RANKING_CSV):
    if not rows:
        print("[WARN] tidak ada baris untuk CSV.")
        return
    df = pd.DataFrame(rows)
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CSV_COLUMNS].sort_values("Final_Score", ascending=False)
    df.to_csv(path, index=False)
    print(f"[OK] {len(df)} saham -> {path} (urut Final_Score tertinggi).")


BACKTEST_COLUMNS = [
    "tanggal_screening", "ticker", "final_score", "klasifikasi", "fase",
    "entry_price", "sl_price", "tp_price", "alasan_full",
    "skor_akumulasi", "skor_smartmoney", "skor_foreign",
    "skor_volume", "skor_momentum", "risiko_distribusi",
    "price_5d_later", "price_10d_later", "return_5d", "return_10d",
]


def append_backtest_csv(rows: list, now: datetime, path: str = BACKTEST_CSV):
    """Tambah baris baru ke backtest_data.csv (semua saham lolos ambang)."""
    if not rows:
        return
    new_rows = []
    for r in rows:
        new_rows.append({
            "tanggal_screening": f"{now:%Y-%m-%d}",
            "ticker": r["Ticker"],
            "final_score": r["Final_Score"],
            "klasifikasi": r["Klasifikasi"],
            "fase": r["Fase_Wyckoff"],
            "entry_price": r["Entry"],
            "sl_price": r["SL_3pct"],
            "tp_price": r["TP_resistance"],
            "alasan_full": r["Alasan"],
            "skor_akumulasi": r["Skor_Akumulasi"],
            "skor_smartmoney": r["Skor_SmartMoney"],
            "skor_foreign": r["Skor_ForeignFlow_PROXY"],
            "skor_volume": r["Skor_Volume"],
            "skor_momentum": r["Skor_Momentum"],
            "risiko_distribusi": r["RisikoDistribusi"],
            "price_5d_later": "",
            "price_10d_later": "",
            "return_5d": "",
            "return_10d": "",
        })
    df_new = pd.DataFrame(new_rows, columns=BACKTEST_COLUMNS)
    if os.path.exists(path):
        df_new.to_csv(path, mode="a", header=False, index=False)
    else:
        df_new.to_csv(path, mode="w", header=True, index=False)
    print(f"[OK] {len(df_new)} baris -> {path} (append, utk backtest).")


def build_message(qualified: list, n_liquid: int, macro: dict, now: datetime) -> str:
    header = f"📊 IDX SCREENING — {now:%d-%m-%Y %H:%M}"
    macro_line = macro_context_line(macro)
    picks = qualified[:MAX_PICKS]
    n_qualified = len(qualified)
    footer = (
        "* Foreign Flow = PROXY, verifikasi RTI\n"
        f"📈 {n_qualified} saham lolos ambang dari {n_liquid} likuid\n"
        "⚠️ Sistem audit mode: threshold longgar untuk backtest"
    )

    if not picks:
        return (f"{header}\n{macro_line}\n\n"
                "🔴 TIDAK ADA SETUP BERKUALITAS HARI INI — CASH IS POSITION\n\n"
                f"{footer}")

    tier_titles = {"⭐⭐⭐": "HIGH CONVICTION", "⭐⭐": "GOOD SETUP", "⭐": "WATCHLIST"}
    counts = {"⭐⭐⭐": 0, "⭐⭐": 0, "⭐": 0}
    for s in qualified:
        if s["Bintang"] in counts:
            counts[s["Bintang"]] += 1

    lines = [header, macro_line, ""]
    idx = 0
    for bintang in ("⭐⭐⭐", "⭐⭐", "⭐"):
        tier_picks = [s for s in picks if s["Bintang"] == bintang]
        if not tier_picks:
            continue
        lines.append(f"{bintang} {tier_titles[bintang]}:")
        for s in tier_picks:
            idx += 1
            kalimat = s["Alasan"].split(". ")[0]
            if bintang == "⭐":
                lines.append(f"{idx}. {s['Ticker']} | {s['Final_Score']:.0f}/100 — {kalimat}")
            else:
                vol20_m = s["Value_20d_Rp"] / 1_000_000_000
                lines.append(f"{idx}. {s['Ticker']} | {s['Final_Score']:.0f}/100")
                lines.append(f"   Fase: {s['Fase_Wyckoff']} | RSI: {s['RSI']:.0f} | "
                             f"5d: {s['Change5d%']:+.1f}% | 20d Vol: Rp{vol20_m:.1f}M")
                lines.append(f"   Akum: {s['Skor_Akumulasi']:.0f} SM: {s['Skor_SmartMoney']:.0f} "
                             f"Foreign*: {s['Skor_ForeignFlow_PROXY']:.0f} Vol: {s['Skor_Volume']:.0f} "
                             f"Mom: {s['Skor_Momentum']:.0f} Risk: -{s['RisikoDistribusi']:.0f}")
                lines.append(f"   Entry: {fmt(s['Entry'])} | SL: -3% ({fmt(s['SL_3pct'])}) "
                             f"| TP: {fmt(s['TP_resistance'])}")
                lines.append(f"   {kalimat}")
            lines.append("")
        lines.append("")

    lines.append(f"📊 Total kandidat: ⭐⭐⭐ {counts['⭐⭐⭐']} | ⭐⭐ {counts['⭐⭐']} | ⭐ {counts['⭐']}")
    lines.append(footer)
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    if not token:
        print("[WARN] TELEGRAM_BOT_TOKEN belum di-set — pesan TIDAK dikirim.\n")
        print(text)
        return False
    try:
        resp = requests.post(TELEGRAM_API.format(token=token),
                             data={"chat_id": chat_id, "text": text}, timeout=15)
        resp.raise_for_status()
        print(f"[OK] Alert terkirim ke Telegram (chat {chat_id}).")
        return True
    except Exception as exc:                             # noqa: BLE001
        print(f"[ERROR] gagal kirim Telegram: {exc}")
        print(text)
        return False


def append_log(status: str, total: int, n_liquid: int, n_qualified: int,
                picks: list, telegram_status: str, error_msg: str = None):
    """Tulis 1 entri ke trading_log.txt (audit jejak tiap eksekusi)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if picks:
        picks_str = ", ".join(f"{p['Ticker']}({p['Final_Score']:.0f})" for p in picks)
    else:
        picks_str = "-"
    lines = [
        f"[{ts}] {status} | Lolos likuiditas: {n_liquid}/{total} | Lolos ambang: {n_qualified}",
        f"  Top picks: {picks_str}",
        f"  Telegram: {telegram_status}",
    ]
    if error_msg:
        lines.append(f"  Error: {error_msg}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as exc:                             # noqa: BLE001
        print(f"[WARN] gagal tulis {LOG_FILE}: {exc}")


def check_internet(timeout: int = 5) -> bool:
    """Cek koneksi internet umum (best-effort GET ke beberapa endpoint)."""
    targets = ("https://www.google.com", "https://1.1.1.1")
    for url in targets:
        try:
            requests.get(url, timeout=timeout)
            return True
        except Exception:                                # noqa: BLE001
            continue
    return False


def wait_for_internet(max_attempts: int = 3, delay: int = 30) -> bool:
    """Cek internet, kalau gagal tunggu `delay` detik & coba lagi max_attempts kali."""
    for i in range(max_attempts):
        if check_internet():
            if i > 0:
                print(f"[OK] internet kembali tersambung setelah percobaan ke-{i + 1}.")
            return True
        if i < max_attempts - 1:
            print(f"[WARN] internet down, retry {i + 1}/{max_attempts} setelah {delay}s...")
            time.sleep(delay)
    return False


# ===========================================================================
# PIPELINE INTI
# ===========================================================================

def screen(data_map: dict, sektor_map: dict, ihsg_close, now: datetime) -> tuple:
    """Return (rows_lolos_filter, n_skip_likuiditas)."""
    rows, skipped = [], 0
    for code, df in data_map.items():
        close = df["Close"].astype(float)
        volume = df["Volume"].astype(float)
        value_20d = value_20d_rp(close, volume)
        if value_20d < MIN_VALUE_20D:                    # hard filter likuiditas
            skipped += 1
            continue
        sektor = sektor_map.get(code, "LAINNYA")
        try:
            res = score_stock(df, sektor, ihsg_close, value_20d)
        except Exception as exc:                         # noqa: BLE001
            print(f"  {code}: gagal skoring ({exc})")
            continue
        res.update({"Ticker": code, "Tanggal": f"{now:%Y-%m-%d}", "Jam": f"{now:%H:%M}"})
        rows.append(res)
    rows.sort(key=lambda r: r["Final_Score"], reverse=True)
    return rows, skipped


# ===========================================================================
# MAIN
# ===========================================================================

def run(send=True):
    now = datetime.now()
    codes = load_top100()
    sektor_map = load_sektor()
    macro = load_macro()
    total = len(codes)
    print(f"Screening Wyckoff/SMC {total} saham IDX — {now:%Y-%m-%d %H:%M}")
    print("Catatan: skor Akumulasi/SmartMoney/ForeignFlow = PROXY dari OHLCV.\n")

    # Pre-flight: internet WAJIB ada sebelum unduh Yahoo Finance.
    # Gagal 3x -> log INTERNET_DOWN dan exit 1 supaya Task Scheduler retry.
    print("Cek koneksi internet...")
    if not wait_for_internet(max_attempts=3, delay=30):
        print("[ERROR] INTERNET_DOWN — screening dibatalkan.")
        alert = (f"⚠️ {now:%d-%m-%Y %H:%M} INTERNET DOWN\n"
                 "Screening IDX dibatalkan. Task Scheduler akan retry "
                 "sesuai jadwal RestartOnFailure (tiap 5 mnt, maks 3x).")
        tg = "SKIPPED"
        if send:
            try:
                tg = "SENT" if send_telegram(alert) else "FAILED"
            except Exception:                            # noqa: BLE001
                tg = "FAILED"
        append_log("ERROR", total, 0, 0, [], tg, "INTERNET_DOWN")
        sys.exit(1)
    print("[OK] internet OK.\n")

    status = "SUCCESS"
    n_liquid = 0
    n_qualified = 0
    picks: list = []
    telegram_status = "SKIPPED"
    error_msg = None

    try:
        ihsg_close = fetch_ihsg()
        data_map, gagal = download_all(codes)
        print(f"\nUnduh OK {len(data_map)}/{total}. Gagal: {len(gagal)}")
        if gagal:
            with open(GAGAL_FILE, "w", encoding="utf-8") as fh:
                fh.write("\n".join(gagal) + "\n")
            print(f"[OK] {len(gagal)} ticker gagal -> {GAGAL_FILE}: {gagal}")
            status = "PARTIAL_FAIL"

        rows, skipped = screen(data_map, sektor_map, ihsg_close, now)
        n_liquid = len(rows)
        print(f"Lolos filter likuiditas: {n_liquid} (skip {skipped} karena value20d < Rp3M)")
        save_csv(rows)

        qualified = [r for r in rows if r["Final_Score"] >= SCORE_THRESHOLD]
        n_qualified = len(qualified)
        picks = qualified[:MAX_PICKS]
        append_backtest_csv(qualified, now)
        message = build_message(qualified, n_liquid, macro, now)

        print("\n" + "=" * 52)
        print(message)
        print("=" * 52 + "\n")

        if send:
            telegram_status = "SENT" if send_telegram(message) else "FAILED"
        else:
            telegram_status = "SKIPPED"
    except Exception as exc:                             # noqa: BLE001
        status = "ERROR"
        error_msg = str(exc)
        print(f"[ERROR] run() gagal: {exc}")
    finally:
        append_log(status, total, n_liquid, n_qualified, picks, telegram_status, error_msg)


# ===========================================================================
# SELF-TEST OFFLINE
# ===========================================================================

def _synthetic(kind: str, n: int = 160) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(kind)) % (2**32))
    if kind == "akumulasi_matang":
        # turun -> sideways tight panjang dgn volume naik bertahap + spring
        down = np.linspace(1500, 1000, 60)
        side = 1000 + rng.normal(0, 8, n - 60)
        base = np.concatenate([down, side])
        vol = np.concatenate([np.linspace(2e6, 1e6, 60),
                              np.linspace(1e6, 2.2e6, n - 60)])
    elif kind == "markup_awal":
        side = 1000 + rng.normal(0, 8, n - 30)
        up = np.linspace(1000, 1130, 30)
        base = np.concatenate([side, up])
        vol = np.concatenate([np.full(n - 30, 1.2e6), np.linspace(1.5e6, 3e6, 30)])
    elif kind == "sudah_lari":
        base = np.concatenate([np.full(n - 12, 1000.0), np.linspace(1000, 1400, 12)])
        vol = np.concatenate([np.full(n - 12, 1e6), np.linspace(3e6, 6e6, 12)])
    elif kind == "distribusi":
        up = np.linspace(1000, 1500, n - 20)
        top = 1500 - np.linspace(0, 120, 20) + rng.normal(0, 20, 20)
        base = np.concatenate([up, top])
        vol = np.concatenate([np.linspace(1e6, 2e6, n - 20), np.linspace(3e6, 4.5e6, 20)])
    else:  # sepi/sideways
        base = 1000 + rng.normal(0, 5, n)
        vol = np.full(n, 1.1e6)
    vol = vol * 5                                        # naikkan agar lolos filter likuiditas
    close = base + rng.normal(0, 4, n)
    high = close + rng.uniform(3, 9, n)
    low = close - rng.uniform(3, 9, n)
    # spring untuk akumulasi_matang: satu hari low-tajam, close rebound
    if kind == "akumulasi_matang":
        low[-7] = low[-7] - 40
        close[-7] = (high[-7] + low[-7]) / 2 + 15
    # distribusi: tambah upper-wick tajam di fase puncak
    if kind == "distribusi":
        high[-15:] = high[-15:] + rng.uniform(20, 45, 15)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def selftest():
    print("== SELF-TEST (offline, data sintetis) ==\n")
    kinds = ["akumulasi_matang", "markup_awal", "sudah_lari", "distribusi", "sepi"]
    sektor_map = {k.upper(): "TEKNOLOGI" for k in kinds}
    data_map = {k.upper(): _synthetic(k) for k in kinds}
    ihsg = pd.Series(np.linspace(7000, 7200, 80))
    now = datetime(2026, 6, 10, 15, 30)

    rows, skipped = screen(data_map, sektor_map, ihsg, now)
    print(f"{'TICKER':<18}{'FINAL':>6}{'AKUM':>6}{'SM':>5}{'FF':>5}"
          f"{'VOL':>5}{'MOM':>5}{'RISK':>6}  FASE")
    for r in rows:
        print(f"{r['Ticker']:<18}{r['Final_Score']:>6.1f}{r['Skor_Akumulasi']:>6.0f}"
              f"{r['Skor_SmartMoney']:>5.0f}{r['Skor_ForeignFlow_PROXY']:>5.0f}"
              f"{r['Skor_Volume']:>5.0f}{r['Skor_Momentum']:>5.0f}"
              f"{r['RisikoDistribusi']:>6.0f}  {r['Fase_Wyckoff']}")
    print(f"\n(skip likuiditas: {skipped})")

    # Uji langsung klasifikasi fase & skor dengan kombinasi skor ideal
    print("\n-- uji klasifikasi (input skor langsung) --")
    cases = [
        # akum, vol, mom, risiko, rsi, range_contract, lower_highs, above20
        ("Akumulasi Matang", 78, 55, 35, 20, 65, True, False, False),
        ("Markup Awal",      70, 60, 60, 20, 60, True, False, True),
        ("Markup Kuat",      60, 70, 78, 15, 68, False, False, True),
        ("Akumulasi Awal",   60, 30, 35, 20, 50, False, False, False),
        ("Distribusi Awal",  40, 60, 50, 55, 72, False, False, True),
        ("Distribusi Matang",30, 60, 40, 75, 60, False, True, True),
    ]
    for expect, a_, v_, m_, r_, rsi_, rc_, lh_, ab_ in cases:
        got = classify_phase(a_, v_, m_, r_, rsi_, rc_, lh_, ab_)
        flag = "OK " if got == expect else "XX "
        print(f"  {flag}harap={expect:<18} dapat={got}")
    for sc in (95, 50, 40, 30, 20):
        lab, star = classify_score(sc)
        print(f"  score {sc} -> {star} {lab}")

    print("\nContoh Alasan (saham teratas):")
    print(" ", rows[0]["Alasan"])

    save_csv(rows, path="/tmp/ranking_selftest.csv")

    print("\n-- contoh Telegram (ambang diturunkan utk demo) --")
    print(build_message(rows[:MAX_PICKS], len(rows),
                        {"IHSG_BIAS": "netral", "USD_IDR": "bearish"}, now))
    print("\n-- contoh tidak ada setup --")
    print(build_message([], len(rows), {}, now))
    print("\n[OK] self-test selesai.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run(send="--no-telegram" not in sys.argv)
