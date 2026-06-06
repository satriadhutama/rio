#!/usr/bin/env python3
"""
IDX Stock Monitor — monitor_idx.py
Tarik data saham IDX, hitung indikator teknikal, tampilkan di terminal,
dan simpan ke idx_hasil.csv.

Indikator: EMA20, EMA50, RSI(14), Parabolic SAR, MACD(12,26,9)
"""

import sys, subprocess

def _install(pkg, imp=None):
    try:
        __import__(imp or pkg)
    except ImportError:
        print(f"  Menginstall {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

for _p, _i in [("yfinance","yfinance"),("pandas","pandas"),("tabulate","tabulate")]:
    _install(_p, _i)

import warnings; warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
from tabulate import tabulate
from datetime import datetime

# ─────────────────────────── KONFIGURASI ─────────────────────────────────────

WATCHLIST  = ["BBRI","BMRI","BBCA","BUMI","BRMS","ADMR","AADI"]
PERIOD     = "6mo"       # data 6 bulan terakhir
OUTPUT_CSV = "idx_hasil.csv"

# ──────────────────────────── INDIKATOR ──────────────────────────────────────

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, float("nan")))

def macd(s, fast=12, slow=26, sig=9):
    m = ema(s, fast) - ema(s, slow)
    signal = ema(m, sig)
    return m, signal, m - signal

def psar(hi, lo, af0=0.02, af_step=0.02, af_max=0.20):
    h, l = hi.values, lo.values
    n = len(h)
    out = [0.0]*n
    bull, af, ep = True, af0, l[0]
    out[0] = h[0]
    for i in range(1, n):
        p = out[i-1]
        if bull:
            out[i] = p + af*(ep - p)
            out[i] = min(out[i], l[i-1], l[i-2] if i>=2 else l[i-1])
            if l[i] < out[i]:
                bull, out[i], ep, af = False, ep, l[i], af0
            elif h[i] > ep:
                ep = h[i]; af = min(af+af_step, af_max)
        else:
            out[i] = p + af*(ep - p)
            out[i] = max(out[i], h[i-1], h[i-2] if i>=2 else h[i-1])
            if h[i] > out[i]:
                bull, out[i], ep, af = True, ep, h[i], af0
            elif l[i] < ep:
                ep = l[i]; af = min(af+af_step, af_max)
    return pd.Series(out, index=pd.RangeIndex(n))

# ───────────────────────── SUPPORT / RESISTANCE ───────────────────────────────

def support_resistance(hi, lo, price, lookback=20):
    h = hi.iloc[-lookback:].values
    l = lo.iloc[-lookback:].values
    sh, sl = [], []
    for i in range(1, len(h)-1):
        if h[i] > h[i-1] and h[i] > h[i+1]: sh.append(h[i])
        if l[i] < l[i-1] and l[i] < l[i+1]: sl.append(l[i])
    above = [x for x in sh if x > price]
    res   = min(above) if above else float(hi.iloc[-lookback:].max())
    below = [x for x in sl if x < price]
    sup   = max(below) if below else float(lo.iloc[-lookback:].min())
    return sup, res

# ─────────────────────────── LABEL / SINYAL ──────────────────────────────────

def bias_label(price, e20, e50, sar_val):
    score = sum([price>e20, price>e50, e20>e50, price>sar_val])
    table = {4:("▲▲","BULLISH KUAT"), 3:("▲","BULLISH"),
             2:("→","NETRAL"),        1:("▼","BEARISH"), 0:("▼▼","BEARISH KUAT")}
    ic, lb = table[score]
    return f"{ic} {lb}", score

def rsi_label(r):
    if r >= 70: return f"Overbought ({r:.0f})"
    if r <= 30: return f"Oversold ({r:.0f})"
    return f"Normal ({r:.0f})"

def macd_label(m, sig, m_prev, sig_prev):
    if m_prev <= sig_prev and m > sig: return "⚡Cross↑"
    if m_prev >= sig_prev and m < sig: return "⚡Cross↓"
    return "▲ Bullish" if m > sig else "▼ Bearish"

# ───────────────────────── ANALISA SATU SAHAM ────────────────────────────────

def analyze(sym):
    ticker = sym + ".JK"
    try:
        df = yf.download(ticker, period=PERIOD, progress=False, auto_adjust=True)
    except Exception as e:
        return None, f"Download error: {e}"

    if df is None or df.empty or len(df) < 52:
        return None, f"Data tidak cukup ({len(df) if df is not None else 0} baris)"

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    c = df["Close"].squeeze()
    h = df["High"].squeeze()
    l = df["Low"].squeeze()

    e20  = ema(c, 20);  e50 = ema(c, 50)
    r    = rsi(c, 14)
    s    = psar(h.reset_index(drop=True), l.reset_index(drop=True))
    ml, msl, mh = macd(c)

    price   = float(c.iloc[-1])
    prev    = float(c.iloc[-2])
    pct     = (price - prev) / prev * 100
    e20v    = float(e20.iloc[-1]);  e50v  = float(e50.iloc[-1])
    rv      = float(r.iloc[-1])
    sarv    = float(s.iloc[-1])
    mv      = float(ml.iloc[-1]);   sigv  = float(msl.iloc[-1])
    histv   = float(mh.iloc[-1])
    mprev   = float(ml.iloc[-2]);   sprev = float(msl.iloc[-2])

    sup, res = support_resistance(h, l, price)
    bias, score = bias_label(price, e20v, e50v, sarv)
    macd_lbl    = macd_label(mv, sigv, mprev, sprev)

    return {
        # tabel display
        "Saham"      : sym,
        "Harga"      : f"{price:,.0f}",
        "Chg%"       : f"{pct:+.2f}%",
        "EMA20"      : f"{e20v:,.0f}",
        "EMA50"      : f"{e50v:,.0f}",
        "RSI"        : rsi_label(rv),
        "SAR"        : f"{sarv:,.0f}",
        "MACD"       : macd_lbl,
        "Bias"       : bias,
        # raw
        "_price": price, "_pct": pct,
        "_e20": e20v, "_e50": e50v, "_rsi": rv,
        "_sar": sarv, "_macd": mv, "_sig": sigv, "_hist": histv,
        "_sup": sup, "_res": res, "_score": score,
        "_macd_lbl": macd_lbl,
    }, None

# ─────────────────────────── RINGKASAN ───────────────────────────────────────

def print_summary(rows):
    W = 66
    sep = "  " + "─"*(W-2)

    print()
    print("="*W)
    print("  RINGKASAN ANALISA TEKNIKAL")
    print("="*W)

    for r in rows:
        sym   = r["Saham"]
        p     = r["_price"]
        e20   = r["_e20"];    e50  = r["_e50"]
        rv    = r["_rsi"];    sarv = r["_sar"]
        mv    = r["_macd"];   sigv = r["_sig"]; histv = r["_hist"]
        sup   = r["_sup"];    res  = r["_res"]
        bias  = r["Bias"];    score = r["_score"]
        macdl = r["_macd_lbl"]

        dist_sup = (p - sup) / p * 100
        dist_res = (res - p) / p * 100

        print()
        print(f"  [ {sym} ]  {bias}  ({score}/4 sinyal bullish)")
        print(sep)
        print(f"  Harga    : {p:>10,.0f}   EMA20: {e20:,.0f}  |  EMA50: {e50:,.0f}")
        sar_dir = "di atas SAR → Bullish" if p > sarv else "di bawah SAR → Bearish"
        print(f"  SAR      : {sarv:>10,.0f}   Harga {sar_dir}")

        # RSI
        rsi_note = ("⚠ OVERBOUGHT" if rv>=70 else "⚠ OVERSOLD" if rv<=30 else "Normal")
        print(f"  RSI 14   : {rv:>10.1f}   {rsi_note}")

        # MACD
        hist_sign = "+" if histv >= 0 else ""
        print(f"  MACD     : {mv:>+10.2f}   Signal: {sigv:+.2f}  │  Hist: {hist_sign}{histv:.2f}  │  {macdl}")

        # S/R
        print(f"  Support  : {sup:>10,.0f}   (-{dist_sup:.1f}% dari harga)")
        print(f"  Resist.  : {res:>10,.0f}   (+{dist_res:.1f}% dari harga)")
        print(sep)

    print()

# ─────────────────────────────── MAIN ────────────────────────────────────────

def main():
    W = 66
    print("="*W)
    print("  IDX STOCK MONITOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")
    print("="*W)
    print(f"  Watchlist : {', '.join(WATCHLIST)}")
    print()

    rows, errors = [], []
    for sym in WATCHLIST:
        print(f"  {sym}.JK ...", end=" ", flush=True)
        data, err = analyze(sym)
        if data:
            rows.append(data)
            print("OK")
        else:
            errors.append(sym)
            print(f"GAGAL ({err})")

    if not rows:
        print("\n[ERROR] Tidak ada data. Periksa koneksi internet.")
        sys.exit(1)

    # ── Tabel ringkas ─────────────────────────────────────────────────────────
    cols = ["Saham","Harga","Chg%","EMA20","EMA50","RSI","SAR","MACD","Bias"]
    print()
    print(tabulate(
        [{k: r[k] for k in cols} for r in rows],
        headers="keys", tablefmt="rounded_outline",
        colalign=("left","right","right","right","right","left","right","left","left"),
    ))

    # ── Ringkasan per saham ───────────────────────────────────────────────────
    print_summary(rows)

    # ── Simpan CSV ────────────────────────────────────────────────────────────
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    pd.DataFrame({
        "Saham"          : [r["Saham"]          for r in rows],
        "Harga_IDR"      : [r["_price"]         for r in rows],
        "Perubahan_%"    : [round(r["_pct"],  2) for r in rows],
        "EMA20"          : [round(r["_e20"],  2) for r in rows],
        "EMA50"          : [round(r["_e50"],  2) for r in rows],
        "RSI_14"         : [round(r["_rsi"],  2) for r in rows],
        "SAR"            : [round(r["_sar"],  2) for r in rows],
        "MACD_Line"      : [round(r["_macd"], 2) for r in rows],
        "MACD_Signal"    : [round(r["_sig"],  2) for r in rows],
        "MACD_Histogram" : [round(r["_hist"], 2) for r in rows],
        "MACD_Status"    : [r["_macd_lbl"]       for r in rows],
        "Support"        : [round(r["_sup"],  2) for r in rows],
        "Resistance"     : [round(r["_res"],  2) for r in rows],
        "Bias"           : [r["Bias"]            for r in rows],
        "Skor_Bullish"   : [r["_score"]          for r in rows],
        "Tanggal"        : [now                  for _  in rows],
    }).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"  Hasil tersimpan : {OUTPUT_CSV}")
    if errors:
        print(f"  Gagal diproses  : {', '.join(errors)}")
    print("="*W)

if __name__ == "__main__":
    main()
