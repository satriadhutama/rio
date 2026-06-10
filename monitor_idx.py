"""
monitor_idx.py — Sistem skoring bertingkat (bottom-up) screening top 100 saham IDX.

================================================================
ARSITEKTUR 4 LAYER (tiap layer menghasilkan skor 0-100, bukan filter mati):

    Skor akhir = Bandar x 0.45 + Teknikal x 0.30 + MacroID x 0.15 + Global x 0.10

  Layer 1  Bandarmology proxy (45%) : OBV, ADL, MFI, volume spike, CLV (dari OHLCV)
  Layer 2  Teknikal          (30%) : EMA20/50, RSI, Parabolic SAR, MACD, S/R
  Layer 3  Macro Indonesia   (15%) : IHSG_BIAS + bias sektor (dari macro.txt)
  Layer 4  Global macro      (10%) : USD/IDR, komoditas, risk sentiment (macro.txt)

Catatan jujur: bandarmology "asli" butuh broker summary & net foreign yang TIDAK
tersedia di Yahoo Finance. Layer 1 di sini adalah PROXY berbasis volume-harga.

================================================================
FILE PENDUKUNG (semua bisa kamu edit, tanpa ngoding):
  top100.txt  : 100 ticker IDX (1 per baris, tanpa .JK)
  sektor.txt  : tag sektor   -> TICKER=SEKTOR
  macro.txt   : input makro manual (Layer 3 & 4)

OUTPUT:
  ranking_lengkap.csv : semua 100 saham + skor tiap layer + alasan (untuk audit)
  gagal.txt           : ticker yang gagal diunduh
  Telegram            : maksimal 4 saham teratas dengan skor akhir >= 65

CARA JALANKAN:
  pip install yfinance pandas numpy requests
  export TELEGRAM_BOT_TOKEN="xxxx"        # rahasia, jangan commit
  export TELEGRAM_CHAT_ID="1674060319"    # opsional, ada default
  python monitor_idx.py

  python monitor_idx.py --selftest    # uji logika offline (tanpa internet)
  python monitor_idx.py --no-telegram # screening + CSV saja, tak kirim Telegram
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

DEFAULT_CHAT_ID = "1674060319"          # chat id bukan rahasia -> boleh default
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

TOP100_FILE = "top100.txt"
SEKTOR_FILE = "sektor.txt"
MACRO_FILE = "macro.txt"
RANKING_CSV = "ranking_lengkap.csv"
GAGAL_FILE = "gagal.txt"

# Bobot tiap layer (jumlah = 1.0)
W_BANDAR, W_TEKNIKAL, W_MACRO_ID, W_GLOBAL = 0.45, 0.30, 0.15, 0.10

SCORE_THRESHOLD = 65                    # ambang minimal masuk Telegram
MAX_PICKS = 4                           # maksimal saham dikirim ke Telegram

# Rate-limit Yahoo Finance
BATCH_SIZE = 10
BATCH_PAUSE = 2.5                       # detik antar batch
MAX_RETRY = 3
HISTORY_PERIOD = "6mo"
MIN_BARS = 60                           # minimal bar agar indikator valid

# Pemetaan sektor untuk Layer 4 (global)
COMMODITY_SENSITIVE = {"TAMBANG", "ENERGI"}
EXPORTERS = {"TAMBANG", "ENERGI", "AGRI"}
IMPORTERS = {"KONSUMER", "TEKNOLOGI"}
HIGH_BETA = {"TEKNOLOGI", "TAMBANG", "PROPERTI"}

BIAS_VALUE = {"bullish": 100, "netral": 50, "bearish": 20}


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ===========================================================================
# INDIKATOR DASAR (dihitung manual dari OHLCV)
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


def slope_score(series: pd.Series, lookback: int = 5, max_pts: float = 25.0) -> float:
    """Skor 0..max_pts dari kemiringan (slope) indikator, dinormalisasi volatilitas."""
    if len(series) <= lookback:
        return max_pts * 0.5
    recent = float(series.iloc[-1] - series.iloc[-1 - lookback])
    scale = float(series.diff().tail(20).abs().mean())
    if not scale or np.isnan(scale):
        return max_pts * 0.5
    z = recent / (scale * lookback)
    return float(max_pts / (1 + np.exp(-z)))      # logistik -> 0..max_pts


# ===========================================================================
# LAYER 1 — BANDARMOLOGY PROXY (skor 0-100)
# ===========================================================================

def layer_bandar(df: pd.DataFrame) -> dict:
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    obv_line = obv(close, volume)
    adl_line = adl(high, low, close, volume)
    mfi_now = float(mfi(high, low, close, volume).iloc[-1])

    vol_avg = float(volume.rolling(20).mean().iloc[-1])
    vol_ratio = float(volume.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
    rng = float(high.iloc[-1] - low.iloc[-1])
    clv = (((close.iloc[-1] - low.iloc[-1]) - (high.iloc[-1] - close.iloc[-1])) / rng
           ) if rng > 0 else 0.0
    price_up = bool(close.iloc[-1] > close.iloc[-2])

    obv_up = bool(obv_line.iloc[-1] > obv_line.iloc[-5])
    adl_up = bool(adl_line.iloc[-1] > adl_line.iloc[-5])

    # Sub-skor (total maksimum 100)
    s_obv = slope_score(obv_line, 5, 25)                 # 0..25
    s_adl = slope_score(adl_line, 5, 25)                 # 0..25

    if mfi_now < 20:                                     # oversold -> bonus reversal
        s_mfi = 16.0
    elif mfi_now < 50:
        s_mfi = 6.0 + (mfi_now - 20) / 30 * 6            # 6..12
    else:
        s_mfi = 12.0 + min((mfi_now - 50) / 30, 1) * 8   # 12..20
    s_mfi = clamp(s_mfi, 0, 20)

    if vol_ratio >= 1.5:                                 # lonjakan volume = bandar aktif
        s_vol = 15.0 if price_up else 6.0
    elif vol_ratio >= 1.0:
        base = 8.0 + (vol_ratio - 1.0) / 0.5 * 4         # 8..12
        s_vol = base if price_up else base * 0.5
    else:
        s_vol = 5.0 if price_up else 3.0
    s_vol = clamp(s_vol, 0, 15)

    s_clv = clamp((clv + 1) / 2 * 15, 0, 15)             # 0..15

    score = clamp(s_obv + s_adl + s_mfi + s_vol + s_clv, 0, 100)

    arah = "↑" if (obv_up and adl_up) else ("↓" if (not obv_up and not adl_up) else "→")
    if score >= 60:
        status = "AKUMULASI"
    elif score <= 40:
        status = "DISTRIBUSI"
    else:
        status = "NETRAL"

    return {
        "skor_bandar": round(score, 1),
        "bandar_status": status,
        "bandar_arah": arah,
        "obv_up": obv_up, "adl_up": adl_up,
        "mfi": round(mfi_now, 1), "vol_ratio": round(vol_ratio, 2),
        "clv": round(clv, 2),
    }


# ===========================================================================
# LAYER 2 — TEKNIKAL (skor 0-100)
# ===========================================================================

def layer_teknikal(df: pd.DataFrame) -> dict:
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    ema20 = float(ema(close, 20).iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    rsi_s = rsi(close, 14)
    rsi_now, rsi_prev = float(rsi_s.iloc[-1]), float(rsi_s.iloc[-2])
    sar_now = float(parabolic_sar(high, low).iloc[-1])
    _, _, hist = macd(close)
    hist_now, hist_prev = float(hist.iloc[-1]), float(hist.iloc[-2])
    price = float(close.iloc[-1])
    support, resistance = swing_levels(high, low, price)

    # EMA (maks 25): di atas EMA20 +12, di atas EMA50 +13
    s_ema = (12 if price > ema20 else 0) + (13 if price > ema50 else 0)

    # RSI (maks 20)
    rising = rsi_now > rsi_prev
    if 40 <= rsi_now <= 65:
        s_rsi = 16 + (4 if rising else 0)
    elif rsi_now < 30:
        s_rsi = 14                                       # bonus reversal oversold
    elif 30 <= rsi_now < 40:
        s_rsi = 10 + (2 if rising else 0)
    elif 65 < rsi_now <= 75:
        s_rsi = 10
    else:                                                # >75 overbought = penalti
        s_rsi = 4
    s_rsi = clamp(s_rsi, 0, 20)

    # Parabolic SAR (maks 15)
    s_sar = 15 if sar_now < price else 0

    # MACD (maks 20)
    if hist_now > hist_prev and hist_prev <= 0 < hist_now:
        s_macd = 20                                      # fresh cross up
    elif hist_now > 0:
        s_macd = 15
    elif hist_now > hist_prev:
        s_macd = 10                                      # masih negatif tapi membaik
    else:
        s_macd = 3
    s_macd = clamp(s_macd, 0, 20)

    # Posisi terhadap support-resistance (maks 20): dekat support = upside besar
    if resistance > support:
        pos = (price - support) / (resistance - support)
    else:
        pos = 0.5
    if pos > 1:                                          # breakout di atas resistance
        s_sr = 10
    else:
        s_sr = clamp((1 - pos), 0, 1) * 20

    score = clamp(s_ema + s_rsi + s_sar + s_macd + s_sr, 0, 100)

    return {
        "skor_teknikal": round(score, 1),
        "harga": round(price, 2),
        "ema20": round(ema20, 2), "ema50": round(ema50, 2),
        "rsi": round(rsi_now, 1), "sar": round(sar_now, 2),
        "macd_hist": round(hist_now, 4),
        "support": round(support, 2), "resistance": round(resistance, 2),
        "_above_ema20": price > ema20, "_above_ema50": price > ema50,
        "_macd_pts": s_macd, "_sar_ok": sar_now < price,
    }


# ===========================================================================
# LAYER 3 & 4 — MACRO (dari macro.txt)
# ===========================================================================

def layer_macro_id(macro: dict, sektor: str) -> dict:
    ihsg = BIAS_VALUE.get(macro.get("IHSG_BIAS", "netral"), 50)
    sec_bias = macro.get(f"SEKTOR_{sektor}", "netral")
    sec_val = BIAS_VALUE.get(sec_bias, 50)
    return {"skor_macro_id": round((ihsg + sec_val) / 2, 1),
            "_ihsg": macro.get("IHSG_BIAS", "netral"),
            "_sektor_bias": sec_bias}


def layer_global(macro: dict, sektor: str) -> dict:
    usd = macro.get("USD_IDR", "netral")        # bullish = rupiah kuat
    kom = macro.get("KOMODITAS", "netral")
    risk = macro.get("RISK_SENTIMENT", "netral")
    s = 50.0

    # Komoditas
    if sektor in COMMODITY_SENSITIVE:
        s += {"bullish": 20, "netral": 0, "bearish": -20}.get(kom, 0)
    else:
        s += {"bullish": 3, "netral": 0, "bearish": -3}.get(kom, 0)

    # USD/IDR
    if sektor in EXPORTERS:                     # rupiah lemah (bearish) untungkan eksportir
        s += {"bullish": -10, "netral": 0, "bearish": 12}.get(usd, 0)
    elif sektor in IMPORTERS:                    # rupiah lemah penalti importir
        s += {"bullish": 10, "netral": 0, "bearish": -12}.get(usd, 0)

    # Risk sentiment
    amt = 18 if sektor in HIGH_BETA else 12
    s += {"risk_on": amt, "netral": 0, "risk_off": -amt}.get(risk, 0)

    return {"skor_global": round(clamp(s, 0, 100), 1),
            "_usd": usd, "_kom": kom, "_risk": risk}


# ===========================================================================
# GABUNG SKOR + ALASAN
# ===========================================================================

def build_reason(b: dict, t: dict, m: dict, g: dict, sektor: str) -> str:
    parts = []
    # Bandar
    obv_adl = f"OBV{'↑' if b['obv_up'] else '↓'}/ADL{'↑' if b['adl_up'] else '↓'}"
    vol = f", vol {b['vol_ratio']}x" if b["vol_ratio"] >= 1.5 else ""
    parts.append(f"Bandar {b['bandar_status']}{b['bandar_arah']} "
                 f"({obv_adl}, MFI {b['mfi']:.0f}{vol})")
    # Teknikal
    tek = []
    if t["_above_ema20"] and t["_above_ema50"]:
        tek.append("di atas EMA20&50")
    elif t["_above_ema20"]:
        tek.append("di atas EMA20")
    else:
        tek.append("di bawah EMA20")
    tek.append(f"RSI {t['rsi']:.0f}")
    if t["_macd_pts"] >= 15:
        tek.append("MACD bullish")
    if t["_sar_ok"]:
        tek.append("SAR di bawah")
    parts.append("Teknikal: " + ", ".join(tek))
    # Macro
    parts.append(f"Sektor {sektor} {m['_sektor_bias']}, IHSG {m['_ihsg']}")
    if g["_kom"] != "netral" and sektor in COMMODITY_SENSITIVE:
        parts.append(f"komoditas {g['_kom']}")
    if g["_risk"] != "netral":
        parts.append(f"risk {g['_risk']}")
    return "; ".join(parts)


def score_stock(df: pd.DataFrame, sektor: str, macro: dict) -> dict:
    b = layer_bandar(df)
    t = layer_teknikal(df)
    m = layer_macro_id(macro, sektor)
    g = layer_global(macro, sektor)

    skor_akhir = (b["skor_bandar"] * W_BANDAR +
                  t["skor_teknikal"] * W_TEKNIKAL +
                  m["skor_macro_id"] * W_MACRO_ID +
                  g["skor_global"] * W_GLOBAL)

    price = t["harga"]
    entry = price
    sl = round(entry * 0.97, 2)                          # -3%
    tp = t["resistance"] if t["resistance"] > entry else round(entry * 1.05, 2)

    return {
        "skor_akhir": round(skor_akhir, 1),
        "sektor": sektor,
        **{k: b[k] for k in ("skor_bandar", "bandar_status", "bandar_arah",
                             "mfi", "vol_ratio", "clv")},
        **{k: t[k] for k in ("skor_teknikal", "harga", "ema20", "ema50",
                             "rsi", "sar", "macd_hist", "support", "resistance")},
        "skor_macro_id": m["skor_macro_id"],
        "skor_global": g["skor_global"],
        "entry": entry, "sl": sl, "tp": tp,
        "alasan": build_reason(b, t, m, g, sektor),
    }


# ===========================================================================
# PEMBACAAN FILE KONFIGURASI
# ===========================================================================

def read_lines(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        out = []
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        return out


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
    if not out:
        print(f"[WARN] {MACRO_FILE} tidak ada/kosong -> semua makro dianggap netral.")
    return out


# ===========================================================================
# DOWNLOAD OHLCV (batch + retry + rate-limit)
# ===========================================================================

def fetch_batch(codes: list, period: str = HISTORY_PERIOD) -> dict:
    """Unduh satu batch ticker. Return {code: DataFrame OHLCV} yang berhasil."""
    import yfinance as yf
    tickers = [f"{c}.JK" for c in codes]
    data = yf.download(tickers, period=period, interval="1d", progress=False,
                       auto_adjust=True, group_by="ticker", threads=True)
    out = {}
    for c in codes:
        t = f"{c}.JK"
        try:
            sub = data if len(tickers) == 1 else data[t]
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
    """Unduh semua ticker per batch dengan retry. Return (data_map, gagal_list)."""
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
                print(f"  batch {bi}: {len(remaining)} gagal, retry {attempt+1} "
                      f"dalam {wait:.0f}s -> {remaining}")
                time.sleep(wait)
        if remaining:
            gagal.extend(remaining)
        print(f"[batch {bi}/{len(batches)}] ok={len(batch)-len([c for c in batch if c in gagal])} "
              f"gagal={[c for c in batch if c in gagal]}")
        time.sleep(BATCH_PAUSE)
    return data_map, gagal


# ===========================================================================
# OUTPUT
# ===========================================================================

def fmt(value: float) -> str:
    return f"{value:,.0f}" if value >= 100 else f"{value:.2f}"


CSV_COLUMNS = [
    "tanggal", "jam", "ticker", "sektor", "skor_akhir",
    "skor_bandar", "bandar_status", "bandar_arah",
    "skor_teknikal", "skor_macro_id", "skor_global",
    "harga", "entry", "sl", "tp",
    "rsi", "mfi", "vol_ratio", "clv", "ema20", "ema50",
    "sar", "macd_hist", "support", "resistance", "alasan",
]


def save_csv(rows: list, path: str = RANKING_CSV):
    if not rows:
        print("[WARN] tidak ada baris untuk disimpan ke CSV.")
        return
    df = pd.DataFrame(rows)
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CSV_COLUMNS].sort_values("skor_akhir", ascending=False)
    df.to_csv(path, index=False)
    print(f"[OK] {len(df)} saham disimpan ke {path} (urut skor tertinggi).")


_BANDAR_ICON = {"AKUMULASI": "🟢", "DISTRIBUSI": "🔴", "NETRAL": "⚪"}


def build_message(picks: list, total: int, lolos: int, now: datetime) -> str:
    header = f"📊 IDX TOP PICKS — {now:%d-%m-%Y %H:%M}"
    footer = (f"⚠️ Skor = Bandar45 Teknikal30 Macro25\n"
              f"💡 {lolos} dari {total} saham lolos ambang")

    if not picks:
        return (f"{header}\n\nTIDAK ADA SETUP BERKUALITAS HARI INI\n\n{footer}")

    lines = [header, ""]
    for i, s in enumerate(picks, 1):
        icon = _BANDAR_ICON.get(s["bandar_status"], "⚪")
        lines.append(f"{i}. {s['ticker']} | Skor: {s['skor_akhir']:.0f}/100")
        lines.append(f"   🏦 Bandar: {icon}{s['bandar_status']}{s['bandar_arah']} "
                     f"| 📈 Teknikal: {s['skor_teknikal']:.0f}")
        lines.append(f"   Entry: {fmt(s['entry'])} | SL: {fmt(s['sl'])} (-3%) "
                     f"| TP: {fmt(s['tp'])}")
        lines.append("")
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


# ===========================================================================
# PIPELINE INTI (dipakai run & selftest)
# ===========================================================================

def screen(data_map: dict, sektor_map: dict, macro: dict, now: datetime) -> list:
    rows = []
    for code, df in data_map.items():
        sektor = sektor_map.get(code, "LAINNYA")
        try:
            res = score_stock(df, sektor, macro)
        except Exception as exc:                         # noqa: BLE001
            print(f"  {code}: gagal skoring ({exc})")
            continue
        res.update({"ticker": code, "tanggal": f"{now:%Y-%m-%d}", "jam": f"{now:%H:%M}"})
        rows.append(res)
    rows.sort(key=lambda r: r["skor_akhir"], reverse=True)
    return rows


# ===========================================================================
# MAIN
# ===========================================================================

def run(send=True):
    now = datetime.now()
    codes = load_top100()
    sektor_map = load_sektor()
    macro = load_macro()
    total = len(codes)
    print(f"Screening {total} saham IDX — {now:%Y-%m-%d %H:%M}")
    print(f"Bobot: Bandar {W_BANDAR} | Teknikal {W_TEKNIKAL} | "
          f"MacroID {W_MACRO_ID} | Global {W_GLOBAL}\n")

    data_map, gagal = download_all(codes)
    print(f"\nBerhasil unduh {len(data_map)}/{total} saham. Gagal: {len(gagal)}")
    if gagal:
        with open(GAGAL_FILE, "w", encoding="utf-8") as fh:
            fh.write("\n".join(gagal) + "\n")
        print(f"[OK] {len(gagal)} ticker gagal dicatat ke {GAGAL_FILE}: {gagal}")

    rows = screen(data_map, sektor_map, macro, now)
    save_csv(rows)

    qualified = [r for r in rows if r["skor_akhir"] >= SCORE_THRESHOLD]
    picks = qualified[:MAX_PICKS]
    message = build_message(picks, total, len(qualified), now)

    print("\n" + "=" * 48)
    print(message)
    print("=" * 48 + "\n")
    if send:
        send_telegram(message)


# ===========================================================================
# SELF-TEST OFFLINE (tanpa internet)
# ===========================================================================

def _synthetic(kind: str, n: int = 140) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(kind)) % (2**32))
    if kind == "strong_buy":
        base = np.linspace(900, 1500, n)
        vol = np.linspace(1e6, 3.5e6, n)
    elif kind == "downtrend":
        base = np.linspace(1500, 900, n)
        vol = np.linspace(3e6, 1e6, n)
    elif kind == "oversold":
        base = np.concatenate([np.linspace(1500, 950, n - 12),
                               np.linspace(950, 1010, 12)])
        vol = np.concatenate([np.full(n - 12, 1e6), np.linspace(1.6e6, 3e6, 12)])
    else:  # sideways
        base = 1200 + 30 * np.sin(np.linspace(0, 8 * np.pi, n))
        vol = np.full(n, 1.1e6)
    close = base + rng.normal(0, 6, n)
    high = close + rng.uniform(3, 10, n)
    low = close - rng.uniform(3, 10, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def selftest():
    print("== SELF-TEST (offline, data sintetis) ==\n")
    macro = {"IHSG_BIAS": "bullish", "SEKTOR_BANK": "bullish",
             "SEKTOR_TAMBANG": "bearish", "KOMODITAS": "bullish",
             "USD_IDR": "bearish", "RISK_SENTIMENT": "risk_on"}
    sektor_map = {"AAA": "BANK", "BBB": "TAMBANG", "CCC": "KONSUMER",
                  "DDD": "TEKNOLOGI"}
    data_map = {"AAA": _synthetic("strong_buy"), "BBB": _synthetic("downtrend"),
                "CCC": _synthetic("oversold"), "DDD": _synthetic("sideways")}
    now = datetime(2026, 6, 10, 15, 30)

    rows = screen(data_map, sektor_map, macro, now)
    print(f"{'TICK':<5}{'SEKTOR':<11}{'AKHIR':>7}{'BDR':>6}{'TEK':>6}"
          f"{'MID':>6}{'GLB':>6}  STATUS")
    for r in rows:
        print(f"{r['ticker']:<5}{r['sektor']:<11}{r['skor_akhir']:>7.1f}"
              f"{r['skor_bandar']:>6.1f}{r['skor_teknikal']:>6.1f}"
              f"{r['skor_macro_id']:>6.1f}{r['skor_global']:>6.1f}  "
              f"{r['bandar_status']}{r['bandar_arah']}")
    print("\nContoh alasan (saham teratas):")
    print(" ", rows[0]["alasan"])

    save_csv(rows, path="/tmp/ranking_selftest.csv")

    print("\n-- contoh pesan (ambang diturunkan ke 0 utk demo) --")
    picks = rows[:MAX_PICKS]
    print(build_message(picks, total=100, lolos=len(picks), now=now))
    print("\n-- contoh pesan tidak ada setup --")
    print(build_message([], total=100, lolos=0, now=now))
    print("\n[OK] self-test selesai.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run(send="--no-telegram" not in sys.argv)
