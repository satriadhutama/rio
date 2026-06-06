#!/usr/bin/env python3
"""
IDX Stock Monitor
Memantau saham IDX dengan indikator teknikal: EMA 20, EMA 50, RSI, dan SAR.
Hasil ditampilkan di tabel, ringkasan analisa, dan disimpan ke CSV.
"""

import sys
import subprocess

def install_if_missing(package, import_name=None):
    if import_name is None:
        import_name = package
    try:
        __import__(import_name)
    except ImportError:
        print(f"Menginstall {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package, "--quiet"])

for pkg, imp in [("yfinance", "yfinance"), ("pandas", "pandas"), ("tabulate", "tabulate")]:
    install_if_missing(pkg, imp)

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
from tabulate import tabulate
from datetime import datetime


# ── Konfigurasi ───────────────────────────────────────────────────────────────

WATCHLIST  = ["BBRI", "BMRI", "BBCA", "BUMI", "BRMS", "ADMR", "AADI"]
PERIOD     = "6mo"
SR_LOOKBACK = 20   # candle untuk hitung support/resistance
OUTPUT_CSV = "idx_monitor_result.csv"


# ── Indikator Teknikal ────────────────────────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def calc_sar(high: pd.Series, low: pd.Series,
             af_start: float = 0.02, af_step: float = 0.02,
             af_max: float = 0.20) -> pd.Series:
    h, l = high.values, low.values
    n    = len(h)
    sar  = [0.0] * n
    bull = True
    af   = af_start
    ep   = l[0]
    sar[0] = h[0]

    for i in range(1, n):
        p = sar[i - 1]
        if bull:
            sar[i] = p + af * (ep - p)
            sar[i] = min(sar[i], l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < sar[i]:
                bull, sar[i], ep, af = False, ep, l[i], af_start
            elif h[i] > ep:
                ep = h[i]
                af = min(af + af_step, af_max)
        else:
            sar[i] = p + af * (ep - p)
            sar[i] = max(sar[i], h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar[i]:
                bull, sar[i], ep, af = True, ep, h[i], af_start
            elif l[i] < ep:
                ep = l[i]
                af = min(af + af_step, af_max)

    return pd.Series(sar, index=pd.RangeIndex(n))


# ── Support & Resistance ──────────────────────────────────────────────────────

def calc_support_resistance(high: pd.Series, low: pd.Series,
                            price_now: float, lookback: int = 20):
    """
    Cari support dan resistance dari swing high/low dalam N candle terakhir.
    Swing high: high[i] lebih tinggi dari tetangganya kiri dan kanan.
    Swing low : low[i]  lebih rendah dari tetangganya kiri dan kanan.
    Fallback ke rolling max/min jika tidak ada swing yang valid.
    """
    h = high.iloc[-lookback:].values
    l = low.iloc[-lookback:].values

    swing_highs, swing_lows = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            swing_highs.append(h[i])
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            swing_lows.append(l[i])

    above = [x for x in swing_highs if x > price_now]
    resistance = min(above) if above else float(high.iloc[-lookback:].max())
    r_label    = "Swing High" if above else f"High {lookback}H"

    below = [x for x in swing_lows if x < price_now]
    support  = max(below) if below else float(low.iloc[-lookback:].min())
    s_label  = "Swing Low" if below else f"Low {lookback}H"

    dist_to_r = (resistance - price_now) / price_now * 100
    dist_to_s = (price_now - support)    / price_now * 100

    return support, s_label, resistance, r_label, dist_to_s, dist_to_r


# ── Bias Arah ─────────────────────────────────────────────────────────────────

def calc_bias(price: float, ema20: float, ema50: float, sar: float):
    """
    Hitung bias arah dari 4 sinyal: posisi vs EMA20, EMA50, tren EMA, dan SAR.
    Mengembalikan (label, ikon, skor_bullish, daftar_sinyal_aktif).
    """
    checks = [
        ("Harga > EMA20",  price > ema20),
        ("Harga > EMA50",  price > ema50),
        ("EMA20 > EMA50",  ema20  > ema50),
        ("Harga > SAR",    price > sar),
    ]
    score  = sum(v for _, v in checks)
    active = [k for k, v in checks if v]

    if score == 4:   label, icon = "BULLISH KUAT",  "▲▲"
    elif score == 3: label, icon = "BULLISH",        "▲"
    elif score == 2: label, icon = "NETRAL",         "→"
    elif score == 1: label, icon = "BEARISH",        "▼"
    else:            label, icon = "BEARISH KUAT",   "▼▼"

    return label, icon, score, active


# ── RSI Label ─────────────────────────────────────────────────────────────────

def rsi_summary(rsi: float) -> tuple[str, str]:
    """Kembalikan (label_pendek, komentar_analisa)."""
    if rsi >= 70:
        return f"Overbought ({rsi:.1f})", "⚠ OVERBOUGHT — hati-hati potensi koreksi"
    elif rsi <= 30:
        return f"Oversold ({rsi:.1f})",   "⚠ OVERSOLD — potensi rebound/pembalikan"
    elif rsi >= 60:
        return f"Normal ({rsi:.1f})",     "Momentum positif, belum overbought"
    elif rsi <= 40:
        return f"Normal ({rsi:.1f})",     "Momentum melemah, belum oversold"
    else:
        return f"Normal ({rsi:.1f})",     "Tidak ada kondisi ekstrem"


# ── Analisa Satu Saham ────────────────────────────────────────────────────────

def analyze(ticker_base: str) -> dict | None:
    ticker = ticker_base + ".JK"
    try:
        df = yf.download(ticker, period=PERIOD, progress=False, auto_adjust=True)
    except Exception as e:
        print(f"  [ERROR] Gagal download {ticker}: {e}")
        return None

    if df is None or df.empty or len(df) < 52:
        print(f"  [SKIP] Data tidak cukup untuk {ticker} "
              f"({len(df) if df is not None else 0} baris)")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    rsi   = calc_rsi(close, 14)
    sar   = calc_sar(high.reset_index(drop=True), low.reset_index(drop=True))

    price_now  = float(close.iloc[-1])
    price_prev = float(close.iloc[-2])
    pct_change = (price_now - price_prev) / price_prev * 100

    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    r   = float(rsi.iloc[-1])
    s   = float(sar.iloc[-1])

    support, s_lbl, resistance, r_lbl, dist_s, dist_r = \
        calc_support_resistance(high, low, price_now, SR_LOOKBACK)

    bias_label, bias_icon, bias_score, active_signals = \
        calc_bias(price_now, e20, e50, s)

    rsi_lbl, rsi_comment = rsi_summary(r)

    return {
        # ── untuk tabel ringkas ──
        "Saham"        : ticker_base,
        "Harga (IDR)"  : f"{price_now:,.0f}",
        "Perubahan %"  : f"{pct_change:+.2f}%",
        "EMA 20"       : f"{e20:,.0f}",
        "EMA 50"       : f"{e50:,.0f}",
        "RSI (14)"     : rsi_lbl,
        "SAR"          : f"{s:,.0f}",
        "Bias"         : f"{bias_icon} {bias_label}",
        # ── nilai mentah untuk ringkasan & CSV ──
        "_price"       : price_now,
        "_pct"         : pct_change,
        "_ema20"       : e20,
        "_ema50"       : e50,
        "_rsi"         : r,
        "_sar"         : s,
        "_support"     : support,
        "_s_lbl"       : s_lbl,
        "_dist_s"      : dist_s,
        "_resistance"  : resistance,
        "_r_lbl"       : r_lbl,
        "_dist_r"      : dist_r,
        "_bias_label"  : bias_label,
        "_bias_icon"   : bias_icon,
        "_bias_score"  : bias_score,
        "_active_sig"  : active_signals,
        "_rsi_comment" : rsi_comment,
    }


# ── Tampilan ──────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]) -> None:
    W = 65  # lebar kotak

    def divider(char="─"):
        print("  " + char * (W - 2))

    print()
    print("=" * W)
    print("  RINGKASAN ANALISA TEKNIKAL")
    print("=" * W)

    for r in results:
        sym        = r["Saham"]
        price      = r["_price"]
        e20        = r["_ema20"]
        e50        = r["_ema50"]
        rsi_val    = r["_rsi"]
        sar_val    = r["_sar"]
        support    = r["_support"]
        resistance = r["_resistance"]
        dist_s     = r["_dist_s"]
        dist_r     = r["_dist_r"]
        s_lbl      = r["_s_lbl"]
        r_lbl      = r["_r_lbl"]
        bias       = r["_bias_label"]
        icon       = r["_bias_icon"]
        score      = r["_bias_score"]
        signals    = r["_active_sig"]
        rsi_cmnt   = r["_rsi_comment"]

        print()
        # Header saham
        header = f"  [ {sym} ]  Bias: {icon} {bias}  ({score}/4 sinyal bullish)"
        print(header)
        divider()

        # Harga & EMA
        print(f"  Harga Sekarang : {price:>10,.0f}  "
              f"(EMA20: {e20:,.0f}  |  EMA50: {e50:,.0f})")

        # SAR
        sar_dir = "DI ATAS SAR → Bullish" if price > sar_val else "DI BAWAH SAR → Bearish"
        print(f"  SAR            : {sar_val:>10,.0f}  ({sar_dir})")

        # RSI
        print(f"  RSI 14         : {rsi_val:>10.1f}  — {rsi_cmnt}")

        # Support & Resistance
        print(f"  Support        : {support:>10,.0f}  ({s_lbl}, -{dist_s:.1f}% dari harga)")
        print(f"  Resistance     : {resistance:>10,.0f}  ({r_lbl}, +{dist_r:.1f}% dari harga)")

        # Sinyal aktif
        if signals:
            print(f"  Sinyal Aktif   :  " + "  •  ".join(signals))
        else:
            print(f"  Sinyal Aktif   :  (tidak ada sinyal bullish)")

        divider()

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  IDX STOCK MONITOR")
    print(f"  Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print(f"  Mengunduh data untuk: {', '.join(WATCHLIST)}")
    print()

    results = []
    for sym in WATCHLIST:
        print(f"  Memproses {sym}.JK ...", end=" ", flush=True)
        row = analyze(sym)
        if row:
            results.append(row)
            print("OK")
        else:
            print("GAGAL")

    if not results:
        print("\n[ERROR] Tidak ada data. Periksa koneksi internet.")
        sys.exit(1)

    # ── Tabel ringkas ─────────────────────────────────────────────────────────
    display_cols = [
        "Saham", "Harga (IDR)", "Perubahan %",
        "EMA 20", "EMA 50", "RSI (14)", "SAR", "Bias",
    ]
    display_rows = [{k: r[k] for k in display_cols} for r in results]

    print()
    print(tabulate(
        display_rows, headers="keys", tablefmt="rounded_outline",
        colalign=("left","right","right","right","right","left","right","left"),
    ))

    # ── Ringkasan analisa per saham ───────────────────────────────────────────
    print_summary(results)

    # ── Simpan ke CSV ─────────────────────────────────────────────────────────
    df_out = pd.DataFrame({
        "Saham"         : [r["Saham"]            for r in results],
        "Harga_IDR"     : [r["_price"]           for r in results],
        "Perubahan_%"   : [round(r["_pct"],   2) for r in results],
        "EMA_20"        : [round(r["_ema20"], 2) for r in results],
        "EMA_50"        : [round(r["_ema50"], 2) for r in results],
        "RSI_14"        : [round(r["_rsi"],   2) for r in results],
        "SAR"           : [round(r["_sar"],   2) for r in results],
        "Bias"          : [r["_bias_label"]      for r in results],
        "Skor_Bullish"  : [r["_bias_score"]      for r in results],
        "Support"       : [round(r["_support"],  2) for r in results],
        "Support_Jenis" : [r["_s_lbl"]           for r in results],
        "Dist_Support_%" : [round(r["_dist_s"],  2) for r in results],
        "Resistance"    : [round(r["_resistance"],2) for r in results],
        "Resist_Jenis"  : [r["_r_lbl"]           for r in results],
        "Dist_Resist_%"  : [round(r["_dist_r"],  2) for r in results],
        "Tanggal"       : [datetime.now().strftime("%Y-%m-%d %H:%M") for _ in results],
    })
    df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"  Hasil tersimpan di: {OUTPUT_CSV}")
    print("=" * 65)


if __name__ == "__main__":
    main()
