"""
universe.py — Membangun "universe" (daftar saringan) top-N coin berdasarkan
market cap pada tanggal-tanggal di masa lalu, memakai API CoinGecko.

Cara kerja (dirancang hemat kuota API):
  1. Ambil "kolam kandidat" (candidate pool) berisi coin terbesar saat ini
     (default 1000 coin) lewat endpoint /coins/markets.
  2. Untuk tiap kandidat, ambil riwayat market cap harian SEKALI saja
     (endpoint /coins/{id}/market_chart/range), lalu simpan ke cache lokal
     (folder data/cache/) supaya tidak perlu memanggil API lagi.
  3. Dari data cache itu, susun ranking top-N untuk setiap tanggal yang
     diminta, lalu simpan hasilnya ke data/universe.parquet dan .csv.

KETERBATASAN PENTING (CoinGecko paket gratis/Demo):
  - Riwayat harian hanya tersedia ~365 hari ke belakang.
  - Coin yang sudah mati/delisting SEBELUM hari ini tidak muncul di API
    gratis (daftar coin "inactive" hanya ada di paket berbayar). Artinya
    universe hasil script ini masih mengandung survivorship bias untuk
    coin yang wafat sebelum tanggal pengambilan data. Begitu script ini
    rutin dijalankan, coin yang mati SETELAHNYA tetap terekam di cache,
    jadi bias mengecil seiring waktu.
  - Kuota Demo: ±30 panggilan/menit, 10.000 panggilan/bulan.

Contoh pemakaian (dari terminal, dengan venv aktif):
  python universe.py --start 2025-07-01 --end 2026-06-01 --top 300
  python universe.py --dates 2026-01-01 2026-03-01 --top 300
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Konfigurasi & kunci API
# ---------------------------------------------------------------------------

load_dotenv()  # baca file .env di folder project

API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
# Isi COINGECKO_PLAN=pro di .env kalau Anda berlangganan paket berbayar.
PLAN = os.getenv("COINGECKO_PLAN", "demo").strip().lower()

if PLAN == "pro":
    BASE_URL = "https://pro-api.coingecko.com/api/v3"
    KEY_HEADER = "x-cg-pro-api-key"
    SECONDS_BETWEEN_CALLS = 0.15
else:
    BASE_URL = "https://api.coingecko.com/api/v3"
    KEY_HEADER = "x-cg-demo-api-key"
    # Demo dibatasi ±30 panggilan/menit -> beri jeda aman ±2,1 detik.
    SECONDS_BETWEEN_CALLS = 2.1

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_DIR / "data" / "cache"
OUTPUT_DIR = PROJECT_DIR / "data"

_session = requests.Session()
if API_KEY:
    _session.headers[KEY_HEADER] = API_KEY

_last_call_time = 0.0


def _get(endpoint: str, params: dict | None = None) -> dict | list:
    """Panggil API dengan jeda antar-panggilan dan coba ulang saat kena
    rate limit (HTTP 429) atau gangguan server sementara."""
    global _last_call_time

    url = f"{BASE_URL}{endpoint}"
    for attempt in range(5):
        # Jaga jarak antar panggilan supaya tidak melanggar rate limit.
        wait = SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.monotonic()

        resp = _session.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            pause = int(resp.headers.get("Retry-After", 0)) or 30 * (attempt + 1)
            print(f"  Kena rate limit, istirahat {pause} detik...")
            time.sleep(pause)
            continue
        if resp.status_code in (401, 403):
            raise SystemExit(
                f"API menolak akses ({resp.status_code}). Periksa COINGECKO_API_KEY "
                f"di file .env. Pesan server: {resp.text[:200]}"
            )
        if resp.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        raise SystemExit(f"Error API {resp.status_code} di {endpoint}: {resp.text[:200]}")
    raise SystemExit(f"Gagal memanggil {endpoint} setelah 5 percobaan.")


# ---------------------------------------------------------------------------
# Langkah 1: kolam kandidat
# ---------------------------------------------------------------------------

def get_candidate_pool(pool_size: int = 1000) -> pd.DataFrame:
    """Ambil daftar coin terbesar SAAT INI sebagai kandidat universe.

    Catatan: di paket gratis hanya coin yang masih aktif yang tersedia,
    jadi pool ini adalah batas terbaik yang bisa dicapai tanpa bayar.
    """
    rows = []
    per_page = 250  # maksimum yang diizinkan API
    pages = -(-pool_size // per_page)  # pembulatan ke atas
    for page in range(1, pages + 1):
        print(f"Mengambil kandidat halaman {page}/{pages}...")
        data = _get("/coins/markets", {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
        })
        rows.extend(data)
    df = pd.DataFrame(rows)[["id", "symbol", "name", "market_cap"]]
    df = df.dropna(subset=["id"]).drop_duplicates("id").head(pool_size)
    return df


# ---------------------------------------------------------------------------
# Langkah 2: riwayat market cap per coin (dengan cache)
# ---------------------------------------------------------------------------

def fetch_market_cap_history(coin_id: str, days: int = 365) -> pd.Series | None:
    """Ambil riwayat market cap harian satu coin. Hasil disimpan ke cache;
    panggilan berikutnya membaca dari file, bukan dari API."""
    cache_file = CACHE_DIR / f"{coin_id}.parquet"
    if cache_file.exists():
        return pd.read_parquet(cache_file)["market_cap"]

    now = int(datetime.now(timezone.utc).timestamp())
    data = _get(f"/coins/{coin_id}/market_chart/range", {
        "vs_currency": "usd",
        "from": now - days * 86400,
        "to": now,
    })
    caps = data.get("market_caps") or []
    if not caps:
        return None

    df = pd.DataFrame(caps, columns=["ts_ms", "market_cap"])
    df["date"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.normalize()
    # Ambil nilai terakhir per hari (rentang >90 hari memang harian, tapi
    # titik terakhir bisa berupa data terkini).
    series = df.groupby("date")["market_cap"].last()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    series.to_frame().to_parquet(cache_file)
    return series


# ---------------------------------------------------------------------------
# Langkah 3: susun universe per tanggal
# ---------------------------------------------------------------------------

def build_universe(dates: list[pd.Timestamp], top_n: int = 300,
                   pool_size: int = 1000, history_days: int = 365) -> pd.DataFrame:
    """Bangun ranking top-N untuk setiap tanggal yang diminta.

    Hasil: DataFrame panjang dengan kolom date, rank, id, symbol, name,
    market_cap — satu baris per coin per tanggal.
    """
    pool = get_candidate_pool(pool_size)
    print(f"Kolam kandidat: {len(pool)} coin. Mengunduh riwayat market cap "
          f"(yang sudah ada di cache akan dilewati)...")

    histories: dict[str, pd.Series] = {}
    for i, coin_id in enumerate(pool["id"], start=1):
        if i % 50 == 0 or i == len(pool):
            print(f"  riwayat {i}/{len(pool)} coin...")
        series = fetch_market_cap_history(coin_id, days=history_days)
        if series is not None and len(series) > 0:
            histories[coin_id] = series

    meta = pool.set_index("id")[["symbol", "name"]]
    results = []
    for date in dates:
        date = pd.Timestamp(date).tz_localize("UTC") if date.tzinfo is None else date
        snapshot = {
            cid: s.loc[date] for cid, s in histories.items()
            if date in s.index and pd.notna(s.loc[date]) and s.loc[date] > 0
        }
        if not snapshot:
            print(f"PERINGATAN: tidak ada data untuk {date.date()} "
                  f"(di luar jangkauan riwayat?). Tanggal dilewati.")
            continue
        snap = (pd.Series(snapshot, name="market_cap")
                .sort_values(ascending=False)
                .head(top_n)
                .rename_axis("id")
                .reset_index())
        snap.insert(0, "date", date.date())
        snap.insert(1, "rank", range(1, len(snap) + 1))
        snap = snap.merge(meta, left_on="id", right_index=True, how="left")
        results.append(snap)
        print(f"{date.date()}: top {len(snap)} tersusun "
              f"(no.1: {snap.iloc[0]['name']}).")

    if not results:
        raise SystemExit("Tidak ada satu pun tanggal yang menghasilkan data.")

    universe = pd.concat(results, ignore_index=True)
    universe = universe[["date", "rank", "id", "symbol", "name", "market_cap"]]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(OUTPUT_DIR / "universe.parquet", index=False)
    universe.to_csv(OUTPUT_DIR / "universe.csv", index=False)
    print(f"\nSelesai. Hasil tersimpan di:\n"
          f"  {OUTPUT_DIR / 'universe.parquet'}\n"
          f"  {OUTPUT_DIR / 'universe.csv'}")
    return universe


# ---------------------------------------------------------------------------
# Antarmuka command line
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bangun universe top-N coin per tanggal dari CoinGecko.")
    parser.add_argument("--start", help="Tanggal awal, format YYYY-MM-DD.")
    parser.add_argument("--end", help="Tanggal akhir, format YYYY-MM-DD.")
    parser.add_argument("--freq", default="MS",
                        help="Frekuensi antara start dan end (MS=awal bulan, "
                             "W=mingguan, D=harian). Default: MS.")
    parser.add_argument("--dates", nargs="*",
                        help="Daftar tanggal spesifik YYYY-MM-DD "
                             "(alternatif dari --start/--end).")
    parser.add_argument("--top", type=int, default=300,
                        help="Jumlah coin teratas per tanggal. Default: 300.")
    parser.add_argument("--pool", type=int, default=1000,
                        help="Ukuran kolam kandidat. Default: 1000.")
    parser.add_argument("--history-days", type=int, default=365,
                        help="Berapa hari riwayat diambil per coin "
                             "(Demo maksimal 365). Default: 365.")
    args = parser.parse_args()

    if args.dates:
        dates = [pd.Timestamp(d) for d in args.dates]
    elif args.start and args.end:
        dates = list(pd.date_range(args.start, args.end, freq=args.freq))
    else:
        parser.error("Berikan --dates ATAU pasangan --start dan --end.")

    if not API_KEY:
        print("PERINGATAN: COINGECKO_API_KEY belum diisi di .env. "
              "Tanpa kunci, kuota jauh lebih kecil dan mudah kena blokir.\n")

    build_universe(dates, top_n=args.top, pool_size=args.pool,
                   history_days=args.history_days)


if __name__ == "__main__":
    main()
