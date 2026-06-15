"""
update_backtest.py — Isi price_5d_later / price_10d_later / return_5d / return_10d
di backtest_data.csv dari data Yahoo Finance, lalu cetak laporan win-rate.

CARA PAKAI:
  python update_backtest.py
"""

from datetime import datetime, timedelta
import os

import pandas as pd

BACKTEST_CSV = "backtest_data.csv"


def fetch_price_n_days_later(ticker, screening_date, n):
    import yfinance as yf
    start = screening_date
    end = screening_date + timedelta(days=n * 3 + 5)
    try:
        df = yf.download(f"{ticker}.JK", start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"), interval="1d",
                          progress=False, auto_adjust=True)
        df = df.dropna()
    except Exception as exc:                             # noqa: BLE001
        print(f"  {ticker}: gagal unduh ({exc})")
        return None
    if len(df) <= n:
        return None
    return float(df["Close"].iloc[n])


def update_rows(df: pd.DataFrame) -> pd.DataFrame:
    today = datetime.now().date()
    updated = 0
    for i, row in df.iterrows():
        try:
            screening_date = datetime.strptime(str(row["tanggal_screening"]), "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - screening_date).days
        ticker = row["ticker"]
        entry = float(row["entry_price"])

        if age > 5 and (pd.isna(row["price_5d_later"]) or row["price_5d_later"] == ""):
            price5 = fetch_price_n_days_later(ticker, screening_date, 5)
            if price5 is not None:
                df.at[i, "price_5d_later"] = price5
                df.at[i, "return_5d"] = round((price5 - entry) / entry * 100, 2)
                updated += 1

        if age > 10 and (pd.isna(row["price_10d_later"]) or row["price_10d_later"] == ""):
            price10 = fetch_price_n_days_later(ticker, screening_date, 10)
            if price10 is not None:
                df.at[i, "price_10d_later"] = price10
                df.at[i, "return_10d"] = round((price10 - entry) / entry * 100, 2)
                updated += 1

    print(f"[OK] {updated} field terisi.")
    return df


def print_report(df: pd.DataFrame):
    print("\n=== LAPORAN BACKTEST ===\n")
    for col_return, label in (("return_5d", "5 hari"), ("return_10d", "10 hari")):
        sub = df.dropna(subset=[col_return])
        sub = sub[sub[col_return] != ""]
        if sub.empty:
            print(f"-- {label}: belum ada data --\n")
            continue
        sub = sub.copy()
        sub[col_return] = sub[col_return].astype(float)
        print(f"-- Return {label} --")
        for kelas in sub["klasifikasi"].unique():
            grp = sub[sub["klasifikasi"] == kelas]
            win_rate = (grp[col_return] > 0).mean() * 100
            avg_ret = grp[col_return].mean()
            print(f"  {kelas:<22} n={len(grp):<3} win-rate={win_rate:5.1f}%  avg return={avg_ret:+.2f}%")

        best = sub.loc[sub[col_return].idxmax()]
        worst = sub.loc[sub[col_return].idxmin()]
        print(f"  BEST : {best['ticker']} {best['tanggal_screening']} {col_return}={best[col_return]:+.2f}%")
        print(f"  WORST: {worst['ticker']} {worst['tanggal_screening']} {col_return}={worst[col_return]:+.2f}%")
        print()

    print("-- Pattern (Fase Wyckoff) --")
    sub10 = df.dropna(subset=["return_10d"])
    sub10 = sub10[sub10["return_10d"] != ""]
    if not sub10.empty:
        sub10 = sub10.copy()
        sub10["return_10d"] = sub10["return_10d"].astype(float)
        for fase in sub10["fase"].unique():
            grp = sub10[sub10["fase"] == fase]
            win_rate = (grp["return_10d"] > 0).mean() * 100
            avg_ret = grp["return_10d"].mean()
            if win_rate >= 60:
                tag = "✅ konsisten profit"
            elif win_rate <= 40:
                tag = "❌ konsisten loss"
            else:
                tag = "➖ netral"
            print(f"  {fase:<20} n={len(grp):<3} win-rate={win_rate:5.1f}%  avg={avg_ret:+.2f}%  {tag}")
    else:
        print("  (belum ada data return_10d)")


def main():
    if not os.path.exists(BACKTEST_CSV):
        print(f"[WARN] {BACKTEST_CSV} belum ada. Jalankan monitor_idx.py dulu.")
        return
    df = pd.read_csv(BACKTEST_CSV, dtype={"tanggal_screening": str})
    print(f"Memuat {len(df)} baris dari {BACKTEST_CSV}...")
    df = update_rows(df)
    df.to_csv(BACKTEST_CSV, index=False)
    print_report(df)


if __name__ == "__main__":
    main()
