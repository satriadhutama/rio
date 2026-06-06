#!/usr/bin/env python3
"""
IDX Stock Monitor
Memantau saham IDX dengan indikator teknikal: EMA 20, EMA 50, RSI, dan SAR.
Hasil ditampilkan di tabel dan disimpan ke CSV.
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

# Auto-install dependencies
for pkg, imp in [("yfinance", "yfinance"), ("pandas", "pandas"), ("tabulate", "tabulate")]:
    install_if_missing(pkg, imp)

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
from tabulate import tabulate
from datetime import datetime


# ── Konfigurasi ──────────────────────────────────────────────────────────────

WATCHLIST = ["BBRI", "BMRI", "BBCA", "BUMI", "BRMS", "ADMR", "AADI"]
PERIOD    = "6mo"   # 6 bulan data historis (cukup untuk EMA 50 + RSI)
OUTPUT_CSV = "idx_monitor_result.csv"


# ── Fungsi Indikator Teknikal ─────────────────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    # Wilder smoothing = EMA with alpha=1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_sar(high: pd.Series, low: pd.Series,
             af_start: float = 0.02, af_step: float = 0.02,
             af_max: float = 0.20) -> pd.Series:
    """
    Parabolic SAR.
    Mengembalikan Series berisi nilai SAR untuk setiap baris.
    """
    high  = high.values
    low   = low.values
    n     = len(high)
    sar   = [0.0] * n
    bull  = True          # True = uptrend
    af    = af_start
    ep    = low[0]        # extreme point
    sar[0] = high[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]

        if bull:
            sar[i] = prev_sar + af * (ep - prev_sar)
            # SAR tidak boleh lebih tinggi dari dua low sebelumnya
            if i >= 2:
                sar[i] = min(sar[i], low[i - 1], low[i - 2])
            else:
                sar[i] = min(sar[i], low[i - 1])

            if low[i] < sar[i]:          # tren berbalik ke downtrend
                bull   = False
                sar[i] = ep              # SAR di puncak EP sebelumnya
                ep     = low[i]
                af     = af_start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            sar[i] = prev_sar + af * (ep - prev_sar)
            if i >= 2:
                sar[i] = max(sar[i], high[i - 1], high[i - 2])
            else:
                sar[i] = max(sar[i], high[i - 1])

            if high[i] > sar[i]:         # tren berbalik ke uptrend
                bull   = True
                sar[i] = ep
                ep     = high[i]
                af     = af_start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)

    return pd.Series(sar, index=pd.RangeIndex(n))


# ── Fungsi Label / Sinyal ────────────────────────────────────────────────────

def ema_signal(price: float, ema20: float, ema50: float) -> str:
    if price > ema20 > ema50:
        return "Di atas EMA20 & EMA50"
    elif price > ema20:
        return "Di atas EMA20"
    elif price > ema50:
        return "Di atas EMA50"
    else:
        return "Di bawah EMA20 & EMA50"


def rsi_label(rsi: float) -> str:
    if rsi >= 70:
        return f"Overbought ({rsi:.1f})"
    elif rsi <= 30:
        return f"Oversold ({rsi:.1f})"
    else:
        return f"Normal ({rsi:.1f})"


def sar_signal(price: float, sar: float) -> str:
    return "Bullish (harga > SAR)" if price > sar else "Bearish (harga < SAR)"


# ── Fungsi Utama ──────────────────────────────────────────────────────────────

def analyze(ticker_base: str) -> dict | None:
    ticker = ticker_base + ".JK"
    try:
        df = yf.download(ticker, period=PERIOD, progress=False, auto_adjust=True)
    except Exception as e:
        print(f"  [ERROR] Gagal download {ticker}: {e}")
        return None

    if df is None or df.empty or len(df) < 52:
        print(f"  [SKIP] Data tidak cukup untuk {ticker} ({len(df) if df is not None else 0} baris)")
        return None

    # Flatten MultiIndex kolom jika ada
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    # Hitung indikator
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    rsi   = calc_rsi(close, 14)
    sar   = calc_sar(high.reset_index(drop=True), low.reset_index(drop=True))

    # Nilai terbaru
    price_now   = float(close.iloc[-1])
    price_prev  = float(close.iloc[-2])
    pct_change  = (price_now - price_prev) / price_prev * 100

    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    r   = float(rsi.iloc[-1])
    s   = float(sar.iloc[-1])

    return {
        "Saham"        : ticker_base,
        "Harga (IDR)"  : f"{price_now:,.0f}",
        "Perubahan %"  : f"{pct_change:+.2f}%",
        "EMA 20"       : f"{e20:,.0f}",
        "EMA 50"       : f"{e50:,.0f}",
        "Posisi EMA"   : ema_signal(price_now, e20, e50),
        "RSI (14)"     : rsi_label(r),
        "SAR"          : f"{s:,.0f}",
        "Sinyal SAR"   : sar_signal(price_now, s),
        # Nilai mentah untuk CSV
        "_price"       : price_now,
        "_pct"         : pct_change,
        "_ema20"       : e20,
        "_ema50"       : e50,
        "_rsi"         : r,
        "_sar"         : s,
    }


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
        print("\n[ERROR] Tidak ada data yang berhasil diambil. Periksa koneksi internet.")
        sys.exit(1)

    # ── Tabel tampilan (kolom display saja) ──────────────────────────────────
    display_cols = [
        "Saham", "Harga (IDR)", "Perubahan %",
        "EMA 20", "EMA 50", "Posisi EMA",
        "RSI (14)", "SAR", "Sinyal SAR",
    ]
    display_rows = [{k: r[k] for k in display_cols} for r in results]

    print()
    print(tabulate(display_rows, headers="keys", tablefmt="rounded_outline",
                   colalign=("left","right","right","right","right","left","left","right","left")))

    # ── Simpan ke CSV ─────────────────────────────────────────────────────────
    csv_cols = {
        "Saham"        : [r["Saham"]       for r in results],
        "Harga_IDR"    : [r["_price"]      for r in results],
        "Perubahan_%"  : [round(r["_pct"],  2) for r in results],
        "EMA_20"       : [round(r["_ema20"], 2) for r in results],
        "EMA_50"       : [round(r["_ema50"], 2) for r in results],
        "Posisi_EMA"   : [r["Posisi EMA"]  for r in results],
        "RSI_14"       : [round(r["_rsi"],  2) for r in results],
        "SAR"          : [round(r["_sar"],  2) for r in results],
        "Sinyal_SAR"   : [r["Sinyal SAR"]  for r in results],
        "Tanggal"      : [datetime.now().strftime("%Y-%m-%d %H:%M") for _ in results],
    }
    df_out = pd.DataFrame(csv_cols)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")  # utf-8-sig agar Excel bisa baca langsung

    print()
    print(f"  Hasil tersimpan di: {OUTPUT_CSV}")
    print("=" * 65)


if __name__ == "__main__":
    main()
