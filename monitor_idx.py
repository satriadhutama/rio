"""
monitor_idx.py — Screening sinyal otomatis ~100 saham IHSG + alert Telegram.

Sumber data : Yahoo Finance via yfinance (ticker .JK)
Indikator   : EMA20, RSI(14), Parabolic SAR, MACD(12,26,9) — dihitung manual
Output      : idx_sinyal.csv + pesan alert ke Telegram (pakai requests)

Cara jalankan:
    export TELEGRAM_BOT_TOKEN="xxxx"      # token bot (rahasia, jangan di-commit)
    export TELEGRAM_CHAT_ID="1674060319"  # opsional, default sudah diisi
    pip install yfinance pandas numpy requests
    python monitor_idx.py

Self-test logika tanpa internet:
    python monitor_idx.py --selftest
"""

import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

# CHAT_ID bukan rahasia, jadi boleh jadi default. TOKEN dibaca dari ENV demi
# keamanan (jangan pernah commit token bot ke git).
DEFAULT_CHAT_ID = "1674060319"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

IHSG_TICKER = "^JKSE"
CSV_PATH = "idx_sinyal.csv"

# Watchlist ~100 saham paling likuid IHSG (basis Kompas100 / IDX80 + LQ45).
# Suffix .JK = Bursa Efek Indonesia di Yahoo Finance.
WATCHLIST = [
    # Perbankan & finansial
    "BBCA", "BBRI", "BMRI", "BBNI", "BBTN", "BRIS", "ARTO", "BBHI", "BNGA",
    "BTPS", "NISP", "PNBN", "BJBR", "BJTM", "BFIN", "AMAR",
    # Konsumer & ritel
    "UNVR", "ICBP", "INDF", "MYOR", "KLBF", "SIDO", "HMSP", "GGRM", "AMRT",
    "MAPI", "MAPA", "ACES", "ERAA", "CPIN", "JPFA", "ROTI", "ULTJ", "CMRY",
    "KAEF", "HOKI",
    # Telco, media & teknologi
    "TLKM", "EXCL", "ISAT", "TOWR", "TBIG", "MTEL", "GOTO", "BUKA", "EMTK",
    "MNCN", "SCMA", "WIFI", "DMMX",
    # Energi & pertambangan
    "ADRO", "AADI", "ANTM", "INCO", "PTBA", "PGAS", "MEDC", "ITMG", "INDY",
    "HRUM", "UNTR", "PTRO", "BYAN", "BRMS", "MDKA", "RAJA", "ELSA", "ENRG",
    "DEWA", "TINS", "AMMN", "NCKL", "MBMA", "BUMI", "CUAN", "BREN",
    # Properti & infrastruktur
    "BSDE", "PWON", "CTRA", "SMRA", "ASRI", "JSMR", "PTPP", "ADHI", "WIKA",
    "WSKT", "DMAS", "PANI",
    # Industri dasar, otomotif & manufaktur
    "ASII", "GJTL", "AUTO", "SMGR", "INTP", "INKP", "TKIM", "BRPT", "TPIA",
    "AVIA", "ESSA", "MARK",
    # Kesehatan
    "MIKA", "HEAL", "SILO", "PRDA",
    # Agrikultur
    "AALI", "LSIP", "DSNG", "TAPG", "SSMS", "SGRO",
]


# ---------------------------------------------------------------------------
# Indikator teknikal (implementasi manual, tanpa library 'ta')
# ---------------------------------------------------------------------------

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100)  # loss=0 -> RSI 100


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


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
            # SAR tidak boleh di atas low dua bar terakhir
            sar[i] = min(sar[i], low.iloc[i - 1], low.iloc[max(0, i - 2)])
            if low.iloc[i] < sar[i]:           # reversal ke turun
                trend_up = False
                sar[i] = ep
                ep = low.iloc[i]
                af = af_step
            elif high.iloc[i] > ep:
                ep = high.iloc[i]
                af = min(af + af_step, af_max)
        else:
            sar[i] = max(sar[i], high.iloc[i - 1], high.iloc[max(0, i - 2)])
            if high.iloc[i] > sar[i]:          # reversal ke naik
                trend_up = True
                sar[i] = ep
                ep = high.iloc[i]
                af = af_step
            elif low.iloc[i] < ep:
                ep = low.iloc[i]
                af = min(af + af_step, af_max)

    return pd.Series(sar, index=high.index)


def swing_levels(high: pd.Series, low: pd.Series, price: float,
                 left: int = 2, right: int = 2, lookback: int = 60):
    """Cari support terdekat di bawah harga & resistance terdekat di atas harga
    dari swing pivot (fractal sederhana)."""
    h = high.tail(lookback).reset_index(drop=True)
    l = low.tail(lookback).reset_index(drop=True)
    pivot_high, pivot_low = [], []
    for i in range(left, len(h) - right):
        win_h = h[i - left:i + right + 1]
        win_l = l[i - left:i + right + 1]
        if h[i] == win_h.max():
            pivot_high.append(h[i])
        if l[i] == win_l.min():
            pivot_low.append(l[i])

    supports = [p for p in pivot_low if p < price]
    resistances = [p for p in pivot_high if p > price]
    support = max(supports) if supports else float(low.tail(lookback).min())
    resistance = min(resistances) if resistances else float(high.tail(lookback).max())
    return support, resistance


# ---------------------------------------------------------------------------
# Logika sinyal
# ---------------------------------------------------------------------------

def evaluate_signal(df: pd.DataFrame) -> dict | None:
    """Hitung indikator + tentukan sinyal BUY/LIMIT/NO untuk satu saham."""
    if df is None or len(df) < 40:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    ema20 = ema(close, 20)
    rsi14 = rsi(close, 14)
    sar = parabolic_sar(high, low)
    _, _, hist = macd(close)

    price = float(close.iloc[-1])
    ema_now = float(ema20.iloc[-1])
    rsi_now, rsi_prev = float(rsi14.iloc[-1]), float(rsi14.iloc[-2])
    sar_now = float(sar.iloc[-1])
    hist_now, hist_prev = float(hist.iloc[-1]), float(hist.iloc[-2])

    # 4 syarat bullish
    c_above_ema = price > ema_now
    c_rsi = (30 < rsi_now < 70) and (rsi_now > rsi_prev)          # 30-70 & naik
    c_sar = sar_now < price                                       # SAR di bawah harga
    c_macd = (hist_now > 0) or (hist_now > hist_prev and hist_prev <= 0)  # positif / cross up
    bull_count = sum([c_above_ema, c_rsi, c_sar, c_macd])

    # 4 syarat bearish (kebalikannya)
    b_below_ema = price < ema_now
    b_rsi = (rsi_now < rsi_prev) or (rsi_now > 70)
    b_sar = sar_now > price
    b_macd = (hist_now < 0) or (hist_now < hist_prev and hist_prev >= 0)
    bear_count = sum([b_below_ema, b_rsi, b_sar, b_macd])

    support, resistance = swing_levels(high, low, price)

    # Urutan prioritas: BUY -> LIMIT -> NO
    if bull_count >= 3:
        signal = "BUY"
    elif rsi_now < 30 and hist_now > hist_prev:   # oversold & histogram mulai mengecil
        signal = "LIMIT"
    elif bear_count >= 3 and rsi_now >= 30:
        signal = "NO"
    else:
        signal = "NO"

    # Entry / SL / TP
    if signal == "LIMIT":
        entry = support
    else:
        entry = price
    sl = entry * 0.97                              # -3%
    risk = entry - sl
    tp1 = resistance if resistance > entry else entry * 1.05
    tp2 = entry + 2 * risk                         # risk:reward 1:2

    return {
        "signal": signal,
        "price": price,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": rsi_now,
        "ema20": ema_now,
        "sar": sar_now,
        "macd_hist": hist_now,
        "support": support,
        "resistance": resistance,
        "bull_count": bull_count,
        "bear_count": bear_count,
    }


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------

def fetch_history(ticker: str, period: str = "6mo"):
    import yfinance as yf
    df = yf.download(ticker, period=period, interval="1d",
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    # yfinance bisa balik MultiIndex kolom kalau 1 ticker -> ratakan
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def ihsg_bias() -> str:
    try:
        df = fetch_history(IHSG_TICKER, period="3mo")
        if df is None or len(df) < 20:
            return "unknown"
        close = df["Close"].astype(float)
        return "bullish" if float(close.iloc[-1]) > float(ema(close, 20).iloc[-1]) else "bearish"
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] gagal ambil IHSG: {exc}")
        return "unknown"


# ---------------------------------------------------------------------------
# Format & output
# ---------------------------------------------------------------------------

def fmt(value: float) -> str:
    """Format harga rupiah: tanpa desimal kalau >= 100, else 2 desimal."""
    if value >= 100:
        return f"{value:,.0f}"
    return f"{value:.2f}"


def build_message(buys: list, limits: list, bias: str,
                  valid: int, total: int, now: datetime) -> str:
    header = f"📊 IDX ALERT — {now:%d-%m-%Y %H:%M}"
    bias_icon = {"bullish": "🟢", "bearish": "🔴"}.get(bias, "⚪")

    if not buys and not limits:
        return (f"{header}\n"
                f"🔴 TIDAK ADA SETUP — CASH IS POSITION\n"
                f"⚠️ IHSG: {bias_icon} {bias}")

    lines = [header]
    for s in buys:
        lines.append(f"🟢 BUY: {s['ticker']} @ {fmt(s['entry'])} | "
                     f"SL: {fmt(s['sl'])} | TP: {fmt(s['tp1'])}")
    for s in limits:
        lines.append(f"🟡 LIMIT: {s['ticker']} @ {fmt(s['entry'])} | "
                     f"SL: {fmt(s['sl'])} | TP: {fmt(s['tp1'])}")
    lines.append(f"⚠️ IHSG: {bias_icon} {bias}")
    lines.append(f"💰 Setup valid: {valid} dari {total}")
    return "\n".join(lines)


def save_csv(rows: list, path: str = CSV_PATH):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"[OK] {len(df)} sinyal disimpan ke {path}")


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    if not token:
        print("[WARN] TELEGRAM_BOT_TOKEN belum di-set — pesan tidak dikirim.\n")
        print(text)
        return False
    try:
        resp = requests.post(TELEGRAM_API.format(token=token),
                             data={"chat_id": chat_id, "text": text}, timeout=15)
        resp.raise_for_status()
        print(f"[OK] Alert terkirim ke Telegram (chat {chat_id}).")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] gagal kirim Telegram: {exc}")
        print(text)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    now = datetime.now()
    print(f"Screening {len(WATCHLIST)} saham IHSG — {now:%Y-%m-%d %H:%M}\n")

    bias = ihsg_bias()
    print(f"Bias IHSG: {bias}\n")

    rows, buys, limits = [], [], []
    for i, code in enumerate(WATCHLIST, 1):
        ticker = f"{code}.JK"
        try:
            df = fetch_history(ticker)
            res = evaluate_signal(df)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i:>3}/{len(WATCHLIST)}] {code:<6} ERROR: {exc}")
            continue

        if res is None:
            print(f"[{i:>3}/{len(WATCHLIST)}] {code:<6} data kurang, skip")
            continue

        icon = {"BUY": "🟢", "LIMIT": "🟡", "NO": "🔴"}[res["signal"]]
        print(f"[{i:>3}/{len(WATCHLIST)}] {code:<6} {icon} {res['signal']:<5} "
              f"px={fmt(res['price'])} rsi={res['rsi']:.1f} "
              f"bull={res['bull_count']}")

        row = {"tanggal": f"{now:%Y-%m-%d}", "jam": f"{now:%H:%M}",
               "saham": code, **{k: res[k] for k in
               ("signal", "price", "entry", "sl", "tp1", "tp2",
                "rsi", "ema20", "sar", "macd_hist", "support", "resistance")}}
        rows.append(row)

        entry = {"ticker": code, **res}
        if res["signal"] == "BUY":
            buys.append(entry)
        elif res["signal"] == "LIMIT":
            limits.append(entry)

        time.sleep(0.3)  # jeda sopan agar tidak rate-limited

    save_csv(rows)
    valid = len(buys) + len(limits)
    message = build_message(buys, limits, bias, valid, len(rows) or len(WATCHLIST), now)
    print("\n" + "=" * 40)
    print(message)
    print("=" * 40 + "\n")
    send_telegram(message)


# ---------------------------------------------------------------------------
# Self-test offline (tanpa internet) — verifikasi logika indikator & sinyal
# ---------------------------------------------------------------------------

def _synthetic_df(kind: str, n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    if kind == "uptrend":
        base = np.linspace(1000, 1400, n)
    elif kind == "downtrend":
        base = np.linspace(1400, 1000, n)
    else:  # oversold dip lalu mulai berbalik
        base = np.concatenate([np.linspace(1400, 1000, n - 10),
                               np.linspace(1000, 1030, 10)])
    noise = rng.normal(0, 5, n)
    close = base + noise
    high = close + rng.uniform(2, 8, n)
    low = close - rng.uniform(2, 8, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Close": close, "High": high, "Low": low,
                         "Open": close, "Volume": 1_000_000}, index=idx)


def selftest():
    print("== SELF-TEST (offline, data sintetis) ==\n")
    for kind in ("uptrend", "downtrend", "oversold"):
        df = _synthetic_df(kind)
        res = evaluate_signal(df)
        assert res is not None, f"{kind}: hasil None"
        print(f"{kind:<10} -> {res['signal']:<5} "
              f"price={fmt(res['price'])} entry={fmt(res['entry'])} "
              f"sl={fmt(res['sl'])} tp1={fmt(res['tp1'])} tp2={fmt(res['tp2'])} "
              f"rsi={res['rsi']:.1f} bull={res['bull_count']} bear={res['bear_count']}")

    # Uji format pesan
    now = datetime(2026, 6, 10, 15, 30)
    buys = [{"ticker": "BBCA", "entry": 9800, "sl": 9506, "tp1": 10200}]
    limits = [{"ticker": "ANTM", "entry": 1500, "sl": 1455, "tp1": 1620}]
    print("\n-- contoh pesan ada setup --")
    print(build_message(buys, limits, "bullish", 2, 100, now))
    print("\n-- contoh pesan semua NO --")
    print(build_message([], [], "bearish", 0, 100, now))
    print("\n[OK] self-test selesai.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run()
