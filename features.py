"""
features.py — Menghitung fitur dan label untuk tiap coin di tiap tanggal t0.

ATURAN EMAS (anti "bocor" data masa depan):
  - FITUR hanya boleh memakai data dengan tanggal <= t0.
  - LABEL hanya boleh memakai data dengan tanggal  > t0.
  Pelanggaran aturan ini membuat backtest terlihat hebat padahal bohong.
  Script ini punya dua lapis pengaman:
    1. Setiap fungsi fitur memotong data ke <= t0 lalu MEMVERIFIKASI ulang;
       kalau ada tanggal masa depan lolos, program berhenti dengan error.
    2. Uji sabotase: untuk sampel acak baris, data SETELAH t0 sengaja
       diacak-acak lalu fitur dihitung ulang. Kalau hasil fiturnya berubah,
       berarti fitur diam-diam memakai masa depan -> program error.

FITUR (semua dihitung per coin, per tanggal t0):
  - mcap_rank          : peringkat market cap pada t0 (dari universe.py).
  - age_days           : umur coin = hari sejak data pertama yang kita punya.
                         CATATAN: dengan CoinGecko gratis riwayat maksimal
                         ±365 hari, jadi ini "umur minimal", bukan umur asli.
  - volume_to_mcap     : volume harian / market cap (likuiditas relatif).
  - volatility_30d     : simpangan baku return harian 30 hari, disetahunkan.
  - beta_btc           : kepekaan terhadap gerakan Bitcoin (90 hari).
  - drawdown_from_high : seberapa jauh harga di bawah harga tertinggi yang
                         pernah tercatat sampai t0 (0 = sedang di puncak).
  - ema_distance       : jarak harga ke garis EMA-50 (rata-rata bergerak).

LABEL:
  - label = 1 jika harga TERTINGGI dalam 60 hari setelah t0 naik >100%
    (lebih dari 2x lipat) dibanding harga di t0; selain itu 0.
  - Baris yang jendela 60 harinya belum lengkap (masa depannya belum
    terjadi) DIBUANG, bukan ditebak.

Pemakaian:
  python features.py                 # baca data/universe.parquet & data/cache/
  python features.py --data-dir lain # pakai folder data lain (untuk tes)
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent

HORIZON_DAYS = 60     # jendela masa depan untuk label
THRESHOLD = 1.0       # +100% (harga jadi lebih dari 2x)
VOL_WINDOW = 30       # hari untuk volatilitas
BETA_WINDOW = 90      # hari untuk beta terhadap BTC
EMA_SPAN = 50         # panjang garis EMA
BTC_ID = "bitcoin"

FEATURE_COLS = ["mcap_rank", "age_days", "volume_to_mcap", "volatility_30d",
                "beta_btc", "drawdown_from_high", "ema_distance"]


class LeakageError(Exception):
    """Dilempar kalau terdeteksi pemakaian data dari masa depan."""


# ---------------------------------------------------------------------------
# Pemuatan data
# ---------------------------------------------------------------------------

def load_cache(cache_dir: Path) -> dict[str, pd.DataFrame]:
    """Baca semua riwayat coin dari cache (hasil unduhan universe.py)."""
    histories = {}
    for f in sorted(cache_dir.glob("*.parquet")):
        df = pd.read_parquet(f)
        if "price" not in df.columns:
            print(f"PERINGATAN: {f.name} format lama (tanpa harga), dilewati. "
                  f"Jalankan ulang universe.py untuk mengunduh versi lengkap.")
            continue
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        histories[f.stem] = df.sort_index()
    if not histories:
        raise SystemExit(f"Tidak ada data riwayat di {cache_dir}. "
                         f"Jalankan universe.py dulu.")
    return histories


# ---------------------------------------------------------------------------
# Fitur (hanya boleh melihat data <= t0)
# ---------------------------------------------------------------------------

def _past_only(hist: pd.DataFrame, t0: pd.Timestamp) -> pd.DataFrame:
    """Potong riwayat ke <= t0, lalu verifikasi tidak ada masa depan lolos."""
    past = hist.loc[hist.index <= t0]
    if len(past) and past.index.max() > t0:
        raise LeakageError(
            f"Data bertanggal {past.index.max().date()} lolos ke perhitungan "
            f"fitur untuk t0={t0.date()}. Ini kebocoran masa depan!")
    return past


def compute_features(hist: pd.DataFrame, t0: pd.Timestamp,
                     btc_hist: pd.DataFrame, ema_span: int = EMA_SPAN) -> dict | None:
    """Hitung semua fitur satu coin pada tanggal t0. None = data tak cukup."""
    past = _past_only(hist, t0)
    price = past["price"].dropna()
    if price.empty or price.iloc[-1] <= 0:
        return None
    p0 = price.iloc[-1]
    returns = price.pct_change().dropna()

    feat = {}

    # Umur coin: hari sejak titik data pertama yang kita miliki.
    feat["age_days"] = int((t0 - past.index.min()).days)

    # Likuiditas: volume harian dibanding market cap.
    mcap = past["market_cap"].iloc[-1]
    volume = past["volume"].iloc[-1]
    feat["volume_to_mcap"] = (volume / mcap
                              if pd.notna(mcap) and mcap > 0 and pd.notna(volume)
                              else np.nan)

    # Volatilitas 30 hari, disetahunkan.
    feat["volatility_30d"] = (returns.tail(VOL_WINDOW).std() * np.sqrt(365)
                              if len(returns) >= VOL_WINDOW else np.nan)

    # Beta terhadap BTC: regresi sederhana return coin vs return BTC.
    btc_ret = _past_only(btc_hist, t0)["price"].pct_change().dropna()
    joint = pd.concat([returns, btc_ret], axis=1, join="inner",
                      keys=["coin", "btc"]).tail(BETA_WINDOW)
    btc_var = joint["btc"].var()
    feat["beta_btc"] = (joint["coin"].cov(joint["btc"]) / btc_var
                        if len(joint) >= 30 and pd.notna(btc_var) and btc_var > 0
                        else np.nan)

    # Drawdown: posisi harga relatif terhadap puncak yang pernah tercatat.
    feat["drawdown_from_high"] = p0 / price.max() - 1

    # Jarak harga ke garis EMA.
    ema = price.ewm(span=ema_span, adjust=False).mean().iloc[-1]
    feat["ema_distance"] = p0 / ema - 1 if ema > 0 else np.nan

    return feat


# ---------------------------------------------------------------------------
# Label (hanya boleh melihat data > t0)
# ---------------------------------------------------------------------------

def compute_label(hist: pd.DataFrame, t0: pd.Timestamp,
                  global_last: pd.Timestamp, horizon: int = HORIZON_DAYS,
                  threshold: float = THRESHOLD) -> int | None:
    """1 = harga tertinggi dalam `horizon` hari setelah t0 naik >threshold.
    None = jendela masa depannya belum lengkap (baris harus dibuang)."""
    deadline = t0 + pd.Timedelta(days=horizon)
    if deadline > global_last:
        return None  # masa depan belum terjadi -> jangan menebak

    price = hist["price"].dropna()
    past = price.loc[price.index <= t0]
    if past.empty or past.iloc[-1] <= 0:
        return None
    p0 = past.iloc[-1]

    future = price.loc[(price.index > t0) & (price.index <= deadline)]
    if len(future) and future.index.min() <= t0:
        raise LeakageError(f"Jendela label untuk t0={t0.date()} berisi data "
                           f"masa lalu. Ini salah hitung!")
    if future.empty:
        # Coin berhenti diperdagangkan tepat setelah t0 -> jelas tidak 2x.
        return 0
    return int(future.max() / p0 - 1 > threshold)


# ---------------------------------------------------------------------------
# Uji sabotase: pastikan fitur kebal terhadap perubahan data masa depan
# ---------------------------------------------------------------------------

def leakage_check(samples: list[tuple[str, pd.Timestamp]],
                  histories: dict[str, pd.DataFrame],
                  ema_span: int = EMA_SPAN) -> None:
    """Acak-acak data SETELAH t0 lalu hitung ulang fitur. Kalau hasilnya
    berubah, berarti ada fitur yang mengintip masa depan -> error."""
    rng = np.random.default_rng(42)

    def corrupt(df: pd.DataFrame, t0: pd.Timestamp) -> pd.DataFrame:
        out = df.copy()
        mask = out.index > t0
        if mask.any():
            noise = rng.uniform(0.05, 20.0, size=int(mask.sum()))
            for col in out.columns:
                out.loc[mask, col] = out.loc[mask, col] * noise
        return out

    checked = 0
    for coin_id, t0 in samples:
        hist = histories[coin_id]
        btc = histories[BTC_ID]
        real = compute_features(hist, t0, btc, ema_span)
        fake = compute_features(corrupt(hist, t0), t0, corrupt(btc, t0), ema_span)
        if (real is None) != (fake is None):
            raise LeakageError(f"Fitur {coin_id} @ {t0.date()} berubah saat "
                               f"masa depan diubah: kebocoran terdeteksi!")
        if real is None:
            continue
        for key in real:
            a, b = real[key], fake[key]
            same = (pd.isna(a) and pd.isna(b)) or np.isclose(a, b, equal_nan=True)
            if not same:
                raise LeakageError(
                    f"KEBOCORAN TERDETEKSI: fitur '{key}' untuk {coin_id} "
                    f"@ {t0.date()} berubah ({a} -> {b}) ketika data masa "
                    f"depan diubah. Fitur ini mengintip masa depan!")
        checked += 1
    print(f"Uji anti-bocor: {checked} sampel diacak masa depannya, "
          f"semua fitur tidak berubah. AMAN.")


# ---------------------------------------------------------------------------
# Rangkaian utama
# ---------------------------------------------------------------------------

def build_features(data_dir: Path, ema_span: int = EMA_SPAN,
                   horizon: int = HORIZON_DAYS,
                   threshold: float = THRESHOLD) -> pd.DataFrame:
    universe_file = data_dir / "universe.parquet"
    if not universe_file.exists():
        raise SystemExit(f"{universe_file} tidak ditemukan. Jalankan universe.py dulu.")
    universe = pd.read_parquet(universe_file)
    histories = load_cache(data_dir / "cache")
    if BTC_ID not in histories:
        raise SystemExit("Riwayat Bitcoin tidak ada di cache; beta tidak bisa "
                         "dihitung. Jalankan ulang universe.py.")

    # Tanggal terakhir yang datanya kita punya (untuk tahu label mana yang
    # masa depannya sudah lengkap).
    global_last = max(h.index.max() for h in histories.values())

    rows, censored, missing = [], 0, 0
    for _, urow in universe.iterrows():
        coin_id = urow["id"]
        t0 = pd.Timestamp(urow["date"])
        hist = histories.get(coin_id)
        if hist is None:
            missing += 1
            continue
        feats = compute_features(hist, t0, histories[BTC_ID], ema_span)
        if feats is None:
            missing += 1
            continue
        label = compute_label(hist, t0, global_last, horizon, threshold)
        if label is None:
            censored += 1
            continue
        rows.append({"date": t0.date(), "id": coin_id,
                     "symbol": urow.get("symbol"), "name": urow.get("name"),
                     "mcap_rank": urow["rank"], **feats, "label": label})

    if not rows:
        raise SystemExit(
            "Tidak ada baris berlabel. Kemungkinan besar semua tanggal "
            f"universe terlalu dekat dengan hari ini (jendela {horizon} hari "
            "ke depan belum lengkap). Pakai tanggal universe yang lebih lampau.")

    df = pd.DataFrame(rows)
    df.to_parquet(data_dir / "features.parquet", index=False)
    df.to_csv(data_dir / "features.csv", index=False)

    # Uji sabotase pada sampel acak (maks. 25 baris).
    sample_rows = df.sample(n=min(25, len(df)), random_state=42)
    samples = [(r["id"], pd.Timestamp(r["date"])) for _, r in sample_rows.iterrows()]
    leakage_check(samples, histories, ema_span)

    # Ringkasan untuk manusia.
    base_rate = df["label"].mean() * 100
    print(f"\n===== RINGKASAN =====")
    print(f"Baris berlabel       : {len(df):,} (coin x tanggal)")
    print(f"Dibuang (belum lengkap masa depannya): {censored:,}")
    print(f"Dibuang (data tidak cukup)           : {missing:,}")
    print(f"Naik >{threshold:.0%} dalam {horizon} hari: {int(df['label'].sum()):,} baris")
    print(f"BASE RATE: {base_rate:.2f}% — artinya kalau Anda memilih coin "
          f"secara ASAL/ACAK, peluang dapat yang naik >{threshold:.0%} dalam "
          f"{horizon} hari hanya ±{base_rate:.1f}%. Model/strategi apa pun "
          f"baru berguna kalau bisa memilih lebih baik dari angka ini.")
    print(f"Hasil tersimpan di {data_dir / 'features.csv'} dan .parquet")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hitung fitur & label per coin per tanggal (anti-bocor).")
    parser.add_argument("--data-dir", default=str(PROJECT_DIR / "data"),
                        help="Folder berisi universe.parquet dan cache/.")
    parser.add_argument("--ema-span", type=int, default=EMA_SPAN)
    parser.add_argument("--horizon", type=int, default=HORIZON_DAYS,
                        help="Jendela label, dalam hari. Default 60.")
    parser.add_argument("--threshold", type=float, default=THRESHOLD,
                        help="Ambang kenaikan label (1.0 = +100%%).")
    args = parser.parse_args()
    build_features(Path(args.data_dir), args.ema_span, args.horizon,
                   args.threshold)


if __name__ == "__main__":
    main()
