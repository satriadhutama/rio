"""daily_signal.py — Generator sinyal harian strategi v2 untuk 5 coin
produksi Bybit perpetual: BNB, XRP, ADA, DOGE, TRX.

Cara pakai:
    1. UPDATE DATA DULU:  python deriv_data_layer.py --full
    2. Jalankan:           python daily_signal.py

Output:
    - Tabel ringkasan ke stdout: status + diagnostik per coin, plus
      instruksi entry untuk coin yang ber-sinyal.
    - Arsip JSON di `data/signals/daily_signal_<YYYY-MM-DD>.json`.

Catatan eksekusi:
    Sinyal LAHIR berdasarkan close bar terakhir (= "hari ini" di data).
    Entry akan EKSEKUSI BESOK di OPEN bar berikutnya — saat ini data
    bar besok belum ada, jadi entry/SL/TP yang pasti belum bisa
    dihitung. Console menampilkan ESTIMASI memakai close hari ini
    sebagai proxy; JSON archive menyimpan entry/SL/TP sebagai null.

Skrip ini TIDAK menghubungi API Bybit; hanya membaca parquet di
`data/deriv/{ohlcv,funding,oi}/`. File hilang -> FileNotFoundError.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import indicators
import strategy_trend_v2


COINS = ["BNB", "XRP", "ADA", "DOGE", "TRX"]
RISK_PER_TRADE_PCT = 3.0
ATR_SL_MULT = 2.0
ATR_TP_MULT = 3.0

DATA_DIR = Path("data")
OHLCV_DIR = DATA_DIR / "deriv" / "ohlcv"
FUNDING_DIR = DATA_DIR / "deriv" / "funding"
OI_DIR = DATA_DIR / "deriv" / "oi"
SIGNALS_DIR = DATA_DIR / "signals"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _safe_float(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _safe_str(x) -> str | None:
    return None if x is None or pd.isna(x) else str(x)


def _fmt_pct(value: float | None, decimals: int = 4, signed: bool = True) -> str:
    if value is None:
        return "—"
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.{decimals}f}%"


def _fmt_num(value: float | None, fmt: str = ".6g") -> str:
    if value is None:
        return "—"
    return format(value, fmt)


# ---------------------------------------------------------------------------
# Pembacaan data
# ---------------------------------------------------------------------------

def load_coin(coin: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Baca 3 file parquet untuk satu coin. Crash kalau tidak ada."""
    ohlcv = pd.read_parquet(OHLCV_DIR / f"{coin}.parquet")
    funding = pd.read_parquet(FUNDING_DIR / f"{coin}.parquet")
    oi = pd.read_parquet(OI_DIR / f"{coin}.parquet")
    return ohlcv, funding, oi


# ---------------------------------------------------------------------------
# Evaluasi sinyal bar terakhir
# ---------------------------------------------------------------------------

def evaluate_today(coin: str) -> dict:
    """Evaluasi sinyal v2 untuk bar terakhir di data coin."""
    ohlcv, funding, oi = load_coin(coin)

    # ---- Sentinel trick ----
    # strategy_trend.compute_signals (dipanggil v2) butuh T+1 untuk
    # menerbitkan sinyal di bar T. Bar terbaru di data BELUM punya
    # T+1, jadi kita tempel satu baris sentinel dengan tanggal +1 hari
    # dan open=high=low=close=last_close. Bar terbaru sungguhan jadi
    # berada di iloc[-2] setelah extension; sentinel kita abaikan.
    sentinel = ohlcv.iloc[-1:].copy()
    sentinel_ts = pd.Timestamp(sentinel["timestamp"].iloc[0]) + pd.Timedelta(days=1)
    last_close = float(ohlcv["close"].iloc[-1])
    sentinel.loc[:, "timestamp"] = sentinel_ts
    sentinel.loc[:, "open"] = last_close
    sentinel.loc[:, "high"] = last_close
    sentinel.loc[:, "low"] = last_close
    sentinel.loc[:, "volume"] = 0.0
    extended = pd.concat([ohlcv, sentinel], ignore_index=True)

    ind = indicators.compute_all_indicators(extended)
    sig = strategy_trend_v2.compute_signals_v2(ind, funding, oi)
    row = sig.iloc[-2]

    close = float(row["close"])
    atr_raw = row["atr_14"]
    atr = float(atr_raw) if not pd.isna(atr_raw) else None
    vol_ratio = (atr / close) if (atr is not None and close > 0) else None

    return {
        "coin": coin,
        "date": str(pd.Timestamp(row["timestamp"]).date()),
        "close": close,
        "signal": int(row["signal"]),
        "bias": _safe_str(row["bias"]),
        "filter_reason": _safe_str(row["filter_reason"]),
        # Entry/SL/TP eksekusi besok di open yang belum ada -> null.
        "entry_price": None,
        "sl_price": None,
        "tp_price": None,
        # Audit fields
        "adx_14": _safe_float(row["adx_14"]),
        "atr_14": atr,
        "funding_8h_at_signal": _safe_float(row["funding_8h_at_signal"]),
        "oi_growth_at_signal": _safe_float(row["oi_growth_at_signal"]),
        "vol_ratio": vol_ratio,
    }


# ---------------------------------------------------------------------------
# Status & format baris
# ---------------------------------------------------------------------------

def _status_label(s: dict) -> str:
    sig = s["signal"]
    if sig == 1:
        return "TRADE LONG"
    if sig == -1:
        return "TRADE SHORT"
    # sig == 0
    reason = s["filter_reason"]
    if reason and reason != "passed":
        return f"FILTERED: {reason}"
    return "HOLD"


def format_coin_block(s: dict) -> str:
    """Format satu blok teks per coin: status + diagnostik (+ instruksi
    entry kalau ada sinyal trade)."""
    coin = s["coin"]
    status = _status_label(s)

    diag = (
        f"  close=${_fmt_num(s['close'])}  "
        f"ADX={_fmt_num(s['adx_14'], '.2f')}  "
        f"ATR={_fmt_num(s['atr_14'])}  "
        f"funding={_fmt_pct(s['funding_8h_at_signal'])}  "
        f"oi_growth={_fmt_pct(s['oi_growth_at_signal'], decimals=2)}"
    )

    lines = [f"{coin}: {status}", diag]

    # Instruksi entry hanya untuk TRADE.
    if s["signal"] != 0 and s["atr_14"] is not None:
        side = "LONG" if s["signal"] == 1 else "SHORT"
        close = s["close"]
        atr = s["atr_14"]
        lines.extend([
            f"  GO {side} besok di OPEN bar berikutnya",
            f"  Reference close hari ini: ${close:.6g}",
            (f"  SL = entry - 2*ATR, TP = entry + 3*ATR  (ATR={atr:.6g})"
             if side == "LONG"
             else f"  SL = entry + 2*ATR, TP = entry - 3*ATR  (ATR={atr:.6g})"),
            f"  Risk {RISK_PER_TRADE_PCT:g}% equity per trade",
        ])
    elif s["signal"] != 0 and s["atr_14"] is None:
        lines.append("  (ATR_14 belum tersedia; data terlalu pendek)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print("=" * 72)
    print(f"DAILY SIGNAL v2 — {today_str}")
    print()
    print("⚠️  Update data dulu: python deriv_data_layer.py --full")
    print("=" * 72)
    print()

    results: list[dict] = []
    for coin in COINS:
        results.append(evaluate_today(coin))

    signal_date = results[0]["date"]
    print(f"Tanggal sinyal (close bar terakhir): {signal_date}")
    print("Eksekusi nyata: OPEN bar berikutnya (~00:00 UTC / 07:00 WIB esok)")
    print("-" * 72)

    for s in results:
        print(format_coin_block(s))
        print()

    # ---- Arsip JSON ----
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = SIGNALS_DIR / f"daily_signal_{signal_date}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_date": signal_date,
        "strategy": "v2",
        "universe": COINS,
        "atr_sl_mult": ATR_SL_MULT,
        "atr_tp_mult": ATR_TP_MULT,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "coins": results,
    }
    with out_file.open("w") as f:
        json.dump(payload, f, indent=2)

    print("-" * 72)
    print(f"Arsip JSON: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
