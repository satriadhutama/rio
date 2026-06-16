"""live_paper_report.py — Daily report untuk validasi live paper trading
strategi v2.

Membaca `outputs/trades_v2.parquet`, klasifikasi trade jadi:
  - LIVE: entry_date >= GO_LIVE_DATE (config_live_paper)
  - BACKTEST: entry_date < GO_LIVE_DATE
  - OPEN: live dengan exit_reason == 'open' atau exit_date NaT
  - CLOSED LIVE: live dengan SL/TP/exit_date terisi

Lalu menghitung metrik agregat + per-coin (dibandingkan dengan
BACKTEST_EXPECTATION) dan mencetak laporan harian ke stdout + file
log `outputs/live_paper_log_YYYYMMDD.txt` (append, bukan overwrite).

Checkpoint 30 / 60 trade dilacak di `state/checkpoints_seen.json`
supaya banner "CHECKPOINT REACHED" hanya muncul sekali.

Kode SELALU return 0 (untuk Task Scheduler). Error apa pun di-catch
di __main__, dicetak ke stderr, tapi exit code tetap 0.

Pakai: `python live_paper_report.py`
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config_live_paper as cfg
import metrics as metrics_mod


PROJECT_DIR = Path(__file__).resolve().parent
TRADES_FILE = PROJECT_DIR / "outputs" / "trades_v2.parquet"
OUTPUT_DIR = PROJECT_DIR / "outputs"
STATE_DIR = PROJECT_DIR / "state"
STATE_FILE = STATE_DIR / "checkpoints_seen.json"

INITIAL_CAPITAL = 10_000.0  # mirror $2k * 5 coins dari DEMO_TRADING_WORKFLOW.md
MIN_TRADES_FOR_STATS = 5
LINE = "=" * 64


# ---------------------------------------------------------------------------
# Pembacaan & klasifikasi trade
# ---------------------------------------------------------------------------

def _load_trades() -> pd.DataFrame:
    """Load trades_v2.parquet; kalau file belum ada, kembalikan DataFrame
    kosong dengan kolom yang diharapkan."""
    expected = ["entry_date", "exit_date", "coin", "side", "net_pnl",
                "exit_reason"]
    if not TRADES_FILE.exists():
        return pd.DataFrame(columns=expected)
    df = pd.read_parquet(TRADES_FILE)
    for c in ("entry_date", "exit_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _is_open_row(row) -> bool:
    """Trade dianggap masih terbuka jika exit_reason == 'open' atau
    exit_date NaT."""
    reason = row.get("exit_reason")
    if isinstance(reason, str) and reason == "open":
        return True
    return pd.isna(row.get("exit_date"))


def _classify(trades: pd.DataFrame, go_live: date,
              universe: list[str]) -> dict:
    if "coin" in trades.columns and len(trades):
        trades = trades[trades["coin"].isin(universe)].copy()

    if len(trades) == 0:
        empty = trades.iloc[0:0]
        return {"live_closed": empty, "live_open": empty, "backtest": empty}

    entry_d = pd.to_datetime(trades["entry_date"]).dt.date
    is_live = entry_d >= go_live
    live = trades[is_live].copy()
    backtest = trades[~is_live].copy()

    if len(live):
        open_mask = live.apply(_is_open_row, axis=1).astype(bool)
    else:
        open_mask = pd.Series([], dtype=bool)
    live_open = live[open_mask] if len(live) else live
    live_closed = live[~open_mask] if len(live) else live

    return {"live_closed": live_closed, "live_open": live_open,
            "backtest": backtest}


def _new_today(live_closed: pd.DataFrame, live_open: pd.DataFrame,
               today: date) -> dict:
    closed_today = live_closed.iloc[0:0]
    if len(live_closed):
        exit_d = pd.to_datetime(live_closed["exit_date"]).dt.date
        closed_today = live_closed[exit_d == today]
    opened_today = live_open.iloc[0:0]
    if len(live_open):
        entry_d = pd.to_datetime(live_open["entry_date"]).dt.date
        opened_today = live_open[entry_d == today]
    return {"closed_today": closed_today, "opened_today": opened_today}


# ---------------------------------------------------------------------------
# Perhitungan metrik
# ---------------------------------------------------------------------------

def _build_equity_curve(closed: pd.DataFrame, initial: float) -> pd.DataFrame:
    if len(closed) == 0:
        return pd.DataFrame(columns=["timestamp", "equity"])
    df = closed.sort_values("exit_date").copy()
    df["timestamp"] = pd.to_datetime(df["exit_date"])
    df["equity"] = initial + df["net_pnl"].astype(float).cumsum()
    return df[["timestamp", "equity"]].reset_index(drop=True)


def _compute_aggregate(closed: pd.DataFrame) -> dict:
    n = len(closed)
    out: dict = {
        "total_trades": n,
        "win_pct": None, "pf": None, "avg_win": None, "avg_loss": None,
        "expectancy": None, "sharpe": None,
        "cumulative_pnl_pct": None, "max_drawdown_pct": None,
    }
    if n == 0:
        return out

    pnl = closed["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_pct = len(wins) / n * 100
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None  # negatif

    sum_loss_abs = abs(float(losses.sum())) if len(losses) else 0.0
    if sum_loss_abs == 0:
        pf = float("inf") if len(wins) else None
    else:
        pf = float(wins.sum()) / sum_loss_abs

    wr = win_pct / 100
    if avg_win is not None and avg_loss is not None:
        expectancy = wr * avg_win + (1 - wr) * avg_loss
    elif avg_win is not None:
        expectancy = wr * avg_win
    elif avg_loss is not None:
        expectancy = (1 - wr) * avg_loss
    else:
        expectancy = None

    out.update({
        "win_pct": win_pct, "pf": pf, "avg_win": avg_win,
        "avg_loss": avg_loss, "expectancy": expectancy,
    })

    # Sharpe + max DD lewat metrics.py (butuh equity curve >= 2 titik).
    eq = _build_equity_curve(closed, INITIAL_CAPITAL)
    closed_clean = closed.copy()
    if "exit_reason" in closed_clean.columns:
        closed_clean["exit_reason"] = closed_clean["exit_reason"].fillna("")
    if len(eq) >= 2:
        m = metrics_mod.compute_metrics(closed_clean, eq, INITIAL_CAPITAL)
        out["sharpe"] = m["sharpe"]
        out["cumulative_pnl_pct"] = m["total_return_pct"]
        out["max_drawdown_pct"] = m["max_drawdown_pct"]
    elif len(eq) == 1:
        out["cumulative_pnl_pct"] = (
            (float(eq.iloc[-1]["equity"]) - INITIAL_CAPITAL)
            / INITIAL_CAPITAL * 100
        )
    return out


def _compute_per_coin(closed: pd.DataFrame, universe: list[str]) -> list[dict]:
    rows = []
    for coin in universe:
        sub = closed[closed["coin"] == coin] if len(closed) else closed
        n = len(sub)
        if n == 0:
            wr, pf = None, None
        else:
            pnl = sub["net_pnl"].astype(float)
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            wr = len(wins) / n * 100
            sum_loss_abs = abs(float(losses.sum())) if len(losses) else 0.0
            if sum_loss_abs == 0:
                pf = float("inf") if len(wins) else None
            else:
                pf = float(wins.sum()) / sum_loss_abs

        expected_wr = cfg.BACKTEST_EXPECTATION.get(coin, {}).get("win_pct")
        status = "OK"
        if wr is not None and expected_wr is not None:
            if wr < (expected_wr - cfg.WR_DELTA_ALERT_PP):
                status = "ALERT"

        rows.append({
            "coin": coin, "n": n, "wr": wr, "pf": pf,
            "expected_wr": expected_wr, "status": status,
        })
    return rows


# ---------------------------------------------------------------------------
# Checkpoint state
# ---------------------------------------------------------------------------

def _load_checkpoints_seen() -> set[int]:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return {int(x) for x in data.get("reached", [])}
    except (json.JSONDecodeError, ValueError, OSError):
        return set()


def _save_checkpoints_seen(reached: set[int]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"reached": sorted(reached)}, indent=2))


def _checkpoint_announcements(n_closed: int,
                              checkpoints: list[int]) -> tuple[list[str], set[int]]:
    """Kembalikan (daftar banner baru, set seen yang diperbarui)."""
    seen = _load_checkpoints_seen()
    announcements: list[str] = []
    for ck in checkpoints:
        if n_closed >= ck and ck not in seen:
            announcements.append(f"=== CHECKPOINT {ck} TRADES REACHED ===")
            seen.add(ck)
    return announcements, seen


def _checkpoint_status_line(n_closed: int, checkpoints: list[int]) -> str:
    next_ck = next((c for c in checkpoints if n_closed < c), None)
    if next_ck is None:
        return (f"All checkpoints reached (n_closed = {n_closed} "
                f">= {max(checkpoints)})")
    label = "first" if next_ck == checkpoints[0] else "next"
    return (f"Trade {' / '.join(str(c) for c in checkpoints)}: "
            f"{n_closed}/{next_ck} to {label} checkpoint")


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

def _fmt(x, spec: str = ".2f") -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        if x != x:           # NaN
            return "-"
        if x == float("inf"):
            return "inf"
    return format(x, spec)


def _build_report_text(now_wib: str, counts: dict, today_news: dict,
                       agg: dict, per_coin: list[dict],
                       checkpoint_status: str,
                       announcements: list[str]) -> str:
    L: list[str] = []
    L.append(LINE)
    L.append(f"LIVE PAPER TRADING REPORT — {now_wib}")
    L.append(LINE)

    if announcements:
        L.extend(announcements)
        L.append("")

    L.append(f"Go-live date: {cfg.GO_LIVE_DATE}")
    L.append(f"Days elapsed: {counts['days_elapsed']}")
    L.append(f"Total closed live trades: {counts['closed']}")
    L.append(f"Currently open positions: {counts['open']}")
    L.append("")

    # --- TODAY ---
    L.append("--- TODAY ---")
    closed_today = today_news["closed_today"]
    opened_today = today_news["opened_today"]
    if len(closed_today) == 0:
        L.append("No new trades closed today.")
    else:
        for _, r in closed_today.iterrows():
            L.append(
                f"Closed: {r['coin']:<5} {r.get('side', '?'):<6} "
                f"pnl={_fmt(r['net_pnl'])} reason={r.get('exit_reason', '?')}"
            )
    if len(opened_today) == 0:
        L.append("No new positions opened today.")
    else:
        for _, r in opened_today.iterrows():
            L.append(f"Opened: {r['coin']:<5} {r.get('side', '?'):<6}")
    L.append("")

    # --- OPEN POSITIONS ---
    L.append("--- OPEN POSITIONS ---")
    if counts["open"] == 0:
        L.append("(none)")
    else:
        for _, r in counts["open_df"].iterrows():
            entry_d = pd.to_datetime(r["entry_date"]).date()
            L.append(f"{r['coin']:<5} {r.get('side', '?'):<6} "
                     f"entry_date={entry_d}")
    L.append("")

    # --- AGGREGATE METRICS ---
    L.append(f"--- AGGREGATE METRICS (live, n={agg['total_trades']}) ---")
    if agg["total_trades"] < MIN_TRADES_FOR_STATS:
        L.append(f"Not enough data yet (need >= {MIN_TRADES_FOR_STATS} "
                 f"trades for meaningful stats).")
    else:
        L.append(f"Win rate:           {_fmt(agg['win_pct'])}%")
        L.append(f"Profit factor:      {_fmt(agg['pf'])}")
        L.append(f"Avg win:            {_fmt(agg['avg_win'])}")
        L.append(f"Avg loss:           {_fmt(agg['avg_loss'])}")
        L.append(f"Expectancy/trade:   {_fmt(agg['expectancy'])}")
        L.append(f"Sharpe (ann.):      {_fmt(agg['sharpe'])}")
        L.append(f"Cumulative PnL:     {_fmt(agg['cumulative_pnl_pct'])}%")
        L.append(f"Max drawdown:       {_fmt(agg['max_drawdown_pct'])}%")
    L.append("")

    # --- PER-COIN BREAKDOWN ---
    L.append("--- PER-COIN BREAKDOWN ---")
    L.append(
        f"{'Coin':<6}{'Live Trades':<14}{'WR%':<8}{'PF':<8}"
        f"{'Backtest WR%':<16}{'Status':<8}"
    )
    for r in per_coin:
        L.append(
            f"{r['coin']:<6}"
            f"{r['n']:<14}"
            f"{_fmt(r['wr'], '.1f'):<8}"
            f"{_fmt(r['pf']):<8}"
            f"{_fmt(r['expected_wr'], '.1f'):<16}"
            f"{r['status']:<8}"
        )
    L.append("")

    # --- CHECKPOINT ---
    L.append("--- CHECKPOINT STATUS ---")
    L.append(checkpoint_status)

    return "\n".join(L)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    trades = _load_trades()
    classified = _classify(trades, cfg.GO_LIVE_DATE, cfg.PRODUCTION_UNIVERSE)
    live_closed = classified["live_closed"]
    live_open = classified["live_open"]

    # Tanggal WIB (UTC+7) untuk konsistensi dengan tampilan timestamp.
    now_utc = datetime.now(timezone.utc)
    now_wib_dt = now_utc + timedelta(hours=7)
    now_wib_str = now_wib_dt.strftime("%Y-%m-%d %H:%M WIB")
    today = now_wib_dt.date()

    today_news = _new_today(live_closed, live_open, today)
    agg = _compute_aggregate(live_closed)
    per_coin = _compute_per_coin(live_closed, cfg.PRODUCTION_UNIVERSE)

    days_elapsed = (today - cfg.GO_LIVE_DATE).days
    counts = {
        "days_elapsed": days_elapsed,
        "closed": len(live_closed),
        "open": len(live_open),
        "open_df": live_open,
    }

    announcements, seen_after = _checkpoint_announcements(
        len(live_closed), cfg.CHECKPOINT_TRADES,
    )
    if announcements:
        _save_checkpoints_seen(seen_after)
    checkpoint_status = _checkpoint_status_line(
        len(live_closed), cfg.CHECKPOINT_TRADES,
    )

    report = _build_report_text(
        now_wib_str, counts, today_news, agg, per_coin,
        checkpoint_status, announcements,
    )

    print(report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_file = OUTPUT_DIR / f"live_paper_log_{today.strftime('%Y%m%d')}.txt"
    with log_file.open("a") as f:
        f.write(report)
        f.write("\n\n")

    return 0


if __name__ == "__main__":
    # Selalu exit 0 untuk Task Scheduler. Error apa pun dicetak ke stderr
    # tapi tidak menggagalkan scheduler run.
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
