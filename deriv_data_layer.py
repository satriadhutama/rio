"""deriv_data_layer.py — Bybit perpetual data downloader (OHLCV + funding + OI).

Cara pakai:
    python deriv_data_layer.py --full              # daily (default)
    python deriv_data_layer.py --full --tf 1h
    python deriv_data_layer.py --full --tf 4h

Path output per timeframe:
    daily -> data/deriv/{ohlcv,funding,oi}/{COIN}.parquet
    1h    -> data/deriv/{ohlcv_1h,funding_1h,oi_1h}/{COIN}.parquet
    4h    -> data/deriv/{ohlcv_4h,funding_4h,oi_4h}/{COIN}.parquet

Mode inkremental: hanya bar baru sejak file terakhir yang diunduh.

Funding rate Bybit native 8h. Untuk daily diagregasi ke per-tanggal
(`funding_rate_8h_mean` = rerata 3 fixing per hari, kompatibel dengan
strategy_trend_v2). Untuk 1h/4h di-forward-fill ke setiap target bar
agar tiap baris funding/OI sejajar dengan bar OHLCV.

OI Bybit fetched native sesuai target interval (1h/4h/1d), lalu
forward-fill ke setiap bar.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd


COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "TRX"]
START_DATE = "2018-01-01"

TF_CONFIG = {
    "daily": {
        "ccxt_tf": "1d",
        "ohlcv_dir": Path("data/deriv/ohlcv"),
        "funding_dir": Path("data/deriv/funding"),
        "oi_dir": Path("data/deriv/oi"),
        "oi_interval": "1d",
        "bar_ms": 86_400_000,
    },
    "1h": {
        "ccxt_tf": "1h",
        "ohlcv_dir": Path("data/deriv/ohlcv_1h"),
        "funding_dir": Path("data/deriv/funding_1h"),
        "oi_dir": Path("data/deriv/oi_1h"),
        "oi_interval": "1h",
        "bar_ms": 3_600_000,
    },
    "4h": {
        "ccxt_tf": "4h",
        "ohlcv_dir": Path("data/deriv/ohlcv_4h"),
        "funding_dir": Path("data/deriv/funding_4h"),
        "oi_dir": Path("data/deriv/oi_4h"),
        "oi_interval": "4h",
        "bar_ms": 14_400_000,
    },
}

BATCH_LIMIT = 1000
FUNDING_OI_LIMIT = 200  # Bybit caps per-request lebih kecil


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def symbol_for(coin: str) -> str:
    """Bybit linear USDT perpetual: e.g. BTC/USDT:USDT."""
    return f"{coin}/USDT:USDT"


def to_ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)


def _retry(fn, *args, **kwargs):
    """ccxt call dengan retry exponential backoff (4 kali)."""
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except ccxt.NetworkError as e:
            wait = 2 ** attempt
            print(f"    network glitch ({type(e).__name__}); retry in {wait}s",
                  flush=True)
            time.sleep(wait)
    raise SystemExit("Gagal setelah 4 percobaan jaringan.")


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def _candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low",
                                        "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return (df.drop_duplicates("timestamp")
              .sort_values("timestamp")
              .reset_index(drop=True))


def update_ohlcv(exchange, coin: str, cfg: dict) -> pd.DataFrame | None:
    out_file = cfg["ohlcv_dir"] / f"{coin}.parquet"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists():
        existing = pd.read_parquet(out_file)
        last_ts = pd.Timestamp(existing["timestamp"].max())
        since_ms = int(last_ts.timestamp() * 1000) + cfg["bar_ms"]
    else:
        existing = pd.DataFrame()
        since_ms = to_ms(START_DATE)

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if since_ms >= now_ms:
        print(f"  OHLCV {coin}: sudah terbaru.")
        return existing if not existing.empty else None

    print(f"  OHLCV {coin}: fetch sejak "
          f"{pd.Timestamp(since_ms, unit='ms', tz='UTC')}", flush=True)

    candles: list = []
    cursor = since_ms
    while cursor < now_ms:
        batch = _retry(exchange.fetch_ohlcv, symbol_for(coin),
                       timeframe=cfg["ccxt_tf"], since=cursor,
                       limit=BATCH_LIMIT,
                       params={"category": "linear"})
        if not batch:
            break
        candles.extend(batch)
        last_ts = batch[-1][0]
        next_cursor = last_ts + cfg["bar_ms"]
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(exchange.rateLimit / 1000)

    if not candles:
        print(f"  OHLCV {coin}: tidak ada bar baru.")
        return existing if not existing.empty else None

    new = _candles_to_df(candles)
    combined = (pd.concat([existing, new], ignore_index=True)
                if not existing.empty else new)
    combined = (combined.drop_duplicates("timestamp")
                          .sort_values("timestamp")
                          .reset_index(drop=True))
    combined.to_parquet(out_file, index=False)
    print(f"  OHLCV {coin}: {len(combined)} bars, "
          f"latest={combined['timestamp'].iloc[-1]}", flush=True)
    return combined


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------

def _fetch_funding_history(exchange, coin: str) -> pd.DataFrame:
    """Fetch seluruh funding rate history Bybit native 8h."""
    rows: list = []
    cursor = to_ms(START_DATE)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    while cursor < now_ms:
        batch = _retry(exchange.fetch_funding_rate_history,
                       symbol_for(coin), since=cursor, limit=FUNDING_OI_LIMIT,
                       params={"category": "linear"})
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1]["timestamp"]
        if last_ts <= cursor:
            break
        cursor = last_ts + 1
        time.sleep(exchange.rateLimit / 1000)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    df = pd.DataFrame([{
        "timestamp": pd.to_datetime(r["timestamp"], unit="ms", utc=True),
        "funding_rate": float(r["fundingRate"]),
    } for r in rows])
    return (df.drop_duplicates("timestamp")
              .sort_values("timestamp")
              .reset_index(drop=True))


def update_funding(exchange, coin: str, cfg: dict, ohlcv_df: pd.DataFrame,
                   tf: str) -> None:
    out_file = cfg["funding_dir"] / f"{coin}.parquet"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"  funding {coin}: fetch native 8h history...", flush=True)
    fund = _fetch_funding_history(exchange, coin)
    if fund.empty:
        print(f"  funding {coin}: kosong, skip.")
        return

    if tf == "daily":
        # 3 fixing 8h per hari -> rerata sebagai funding_rate_8h_mean (kompatibel
        # dengan kolom yang dipakai strategy_trend_v2).
        fund["date"] = fund["timestamp"].dt.normalize()
        daily = (fund.groupby("date")["funding_rate"]
                     .mean().reset_index()
                     .rename(columns={"funding_rate": "funding_rate_8h_mean"}))
        out = daily[["date", "funding_rate_8h_mean"]]
    else:
        # 1h/4h: forward-fill ke setiap bar OHLCV.
        bars = (ohlcv_df[["timestamp"]]
                .rename(columns={"timestamp": "date"})
                .sort_values("date")
                .reset_index(drop=True))
        fund_for_merge = (fund.rename(columns={"timestamp": "date",
                                                "funding_rate": "funding_rate_8h_mean"})
                              .sort_values("date")
                              .reset_index(drop=True))
        out = pd.merge_asof(bars, fund_for_merge, on="date", direction="backward")

    out.to_parquet(out_file, index=False)
    print(f"  funding {coin}: {len(out)} rows -> {out_file.name}")


# ---------------------------------------------------------------------------
# Open Interest
# ---------------------------------------------------------------------------

def _fetch_oi_history(exchange, coin: str, interval: str) -> pd.DataFrame:
    rows: list = []
    cursor = to_ms(START_DATE)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    while cursor < now_ms:
        batch = _retry(exchange.fetch_open_interest_history,
                       symbol_for(coin), timeframe=interval,
                       since=cursor, limit=FUNDING_OI_LIMIT,
                       params={"category": "linear"})
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1]["timestamp"]
        if last_ts <= cursor:
            break
        cursor = last_ts + 1
        time.sleep(exchange.rateLimit / 1000)

    if not rows:
        return pd.DataFrame(columns=["timestamp", "open_interest_usd"])
    df = pd.DataFrame([{
        "timestamp": pd.to_datetime(r["timestamp"], unit="ms", utc=True),
        # ccxt: openInterestValue = USD value; openInterestAmount = qty
        "open_interest_usd": float(r.get("openInterestValue")
                                    or r.get("openInterest") or 0.0),
    } for r in rows])
    return (df.drop_duplicates("timestamp")
              .sort_values("timestamp")
              .reset_index(drop=True))


def update_oi(exchange, coin: str, cfg: dict, ohlcv_df: pd.DataFrame,
              tf: str) -> None:
    out_file = cfg["oi_dir"] / f"{coin}.parquet"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"  OI {coin}: fetch interval {cfg['oi_interval']}...", flush=True)
    oi = _fetch_oi_history(exchange, coin, cfg["oi_interval"])
    if oi.empty:
        print(f"  OI {coin}: kosong, skip.")
        return

    if tf == "daily":
        oi["date"] = oi["timestamp"].dt.normalize()
        # Multiple per hari -> ambil terakhir
        out = (oi.groupby("date")[["open_interest_usd"]]
                  .last().reset_index())
    else:
        bars = (ohlcv_df[["timestamp"]]
                .rename(columns={"timestamp": "date"})
                .sort_values("date").reset_index(drop=True))
        oi_for_merge = (oi.rename(columns={"timestamp": "date"})
                          [["date", "open_interest_usd"]]
                          .sort_values("date").reset_index(drop=True))
        out = pd.merge_asof(bars, oi_for_merge, on="date", direction="backward")

    out.to_parquet(out_file, index=False)
    print(f"  OI {coin}: {len(out)} rows -> {out_file.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tf", choices=list(TF_CONFIG.keys()), default="daily",
                        help="Timeframe: daily (default) | 1h | 4h")
    parser.add_argument("--full", action="store_true",
                        help="Full incremental update (default mode).")
    args = parser.parse_args()

    cfg = TF_CONFIG[args.tf]
    print(f"Bybit perpetual downloader  —  tf={args.tf}")
    print(f"  OHLCV dir   : {cfg['ohlcv_dir']}")
    print(f"  Funding dir : {cfg['funding_dir']}")
    print(f"  OI dir      : {cfg['oi_dir']}")
    print()

    exchange = ccxt.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    exchange.load_markets()

    for coin in COINS:
        print(f"== {coin} ==", flush=True)
        ohlcv = update_ohlcv(exchange, coin, cfg)
        if ohlcv is None or len(ohlcv) == 0:
            print(f"  skip {coin}: tidak ada OHLCV")
            print()
            continue
        update_funding(exchange, coin, cfg, ohlcv, args.tf)
        update_oi(exchange, coin, cfg, ohlcv, args.tf)
        print()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
