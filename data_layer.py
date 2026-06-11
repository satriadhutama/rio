"""
data_layer.py — Mengunduh data harga harian (OHLCV) dari Binance lewat
library ccxt, lalu menyimpannya ke folder data/ohlcv/ dalam format parquet.

Sifat penting:
  - INKREMENTAL: kalau file untuk satu coin sudah ada, hanya hari-hari
    baru yang diunduh (lanjutan dari tanggal terakhir di file). Jadi
    menjalankan ulang script ini tidak boros bandwidth.
  - RAMAH RATE LIMIT: ccxt sudah punya pengatur kecepatan bawaan
    (enableRateLimit), dan kita beri jeda kecil antar permintaan.
  - JUJUR PADA TANGGAL TERSEDIA: setiap coin punya tanggal "lahir" di
    Binance yang berbeda (misal SUI baru ada April 2023). Kalau Anda
    minta sejak 2018, script ini tetap meminta sejak 2018; Binance akan
    mengirim data sejak coin itu pertama listing — itu yang kita simpan.

OHLCV = Open, High, Low, Close, Volume (harga buka, tertinggi, terendah,
        penutupan, dan volume perdagangan tiap hari).
"""

import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd

# ---------------------------------------------------------------------------
# Pengaturan
# ---------------------------------------------------------------------------

# Lima coin yang ingin diunduh. Pasangan diperdagangkan terhadap USDT
# (stablecoin USD) karena itu pasangan paling likuid di Binance.
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "SUI/USDT"]

TIMEFRAME = "1d"            # data harian
START_DATE = "2018-01-01"   # mulai dari awal 2018
BATCH_LIMIT = 1000          # Binance maksimal 1000 candle per panggilan

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "data" / "ohlcv"


# ---------------------------------------------------------------------------
# Fungsi pembantu
# ---------------------------------------------------------------------------

def to_ms(date_str_or_ts) -> int:
    """Ubah tanggal jadi milidetik UTC (format yang dipakai Binance)."""
    if isinstance(date_str_or_ts, (int, float)):
        return int(date_str_or_ts)
    ts = pd.Timestamp(date_str_or_ts, tz="UTC")
    return int(ts.timestamp() * 1000)


def symbol_to_filename(symbol: str) -> str:
    """BTC/USDT -> BTC_USDT.parquet (slash dilarang di nama file)."""
    return symbol.replace("/", "_") + ".parquet"


def load_existing(path: Path) -> pd.DataFrame:
    """Baca file lama kalau ada; kalau tidak ada, kembalikan DataFrame kosong."""
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def fetch_ohlcv_paged(exchange: ccxt.Exchange, symbol: str,
                      since_ms: int, end_ms: int) -> list[list]:
    """Ambil OHLCV dari `since_ms` sampai `end_ms` secara bertahap
    (1000 candle per kali). Kembalikan satu list panjang berisi semua
    candle yang berhasil diambil."""
    all_candles: list[list] = []
    cursor = since_ms
    while cursor < end_ms:
        # Coba beberapa kali kalau jaringan rewel.
        for attempt in range(4):
            try:
                batch = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME,
                                             since=cursor, limit=BATCH_LIMIT)
                break
            except ccxt.NetworkError as e:
                wait = 2 ** attempt
                print(f"    Jaringan goyah ({e}); coba lagi dalam {wait}d...")
                time.sleep(wait)
        else:
            raise SystemExit(f"Gagal mengambil {symbol} setelah 4 percobaan.")

        if not batch:
            break  # tidak ada data lagi untuk rentang ini
        all_candles.extend(batch)
        # Maju ke hari SETELAH candle terakhir untuk hindari duplikat.
        last_ts = batch[-1][0]
        next_cursor = last_ts + 86_400_000  # +1 hari dalam milidetik
        if next_cursor <= cursor:
            break  # pengaman supaya tidak loop tak berujung
        cursor = next_cursor
        time.sleep(exchange.rateLimit / 1000)  # patuh pada batas Binance
    return all_candles


def candles_to_dataframe(candles: list[list]) -> pd.DataFrame:
    """Ubah list mentah dari ccxt jadi DataFrame yang rapi."""
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low",
                                        "close", "volume"])
    # timestamp dari Binance dalam milidetik; ubah jadi tanggal UTC.
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    # Buang duplikat (kalau ada) lalu urutkan dari paling lama.
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Inti: ambil/perbarui satu coin
# ---------------------------------------------------------------------------

def download_symbol(exchange: ccxt.Exchange, symbol: str) -> pd.DataFrame:
    """Unduh / perbarui data satu coin. Mengembalikan DataFrame final."""
    out_file = OUTPUT_DIR / symbol_to_filename(symbol)
    existing = load_existing(out_file)

    # Tentukan titik awal: kalau sudah ada file, lanjutkan dari hari setelah
    # tanggal terakhir; kalau belum ada, mulai dari START_DATE.
    if not existing.empty:
        last_ts = pd.Timestamp(existing["timestamp"].max())
        since_ms = int(last_ts.timestamp() * 1000) + 86_400_000
        print(f"  {symbol}: file ada, lanjut dari {last_ts.date() + pd.Timedelta(days=1)}")
    else:
        since_ms = to_ms(START_DATE)
        print(f"  {symbol}: file belum ada, unduh sejak {START_DATE}")

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if since_ms >= now_ms:
        print(f"  {symbol}: sudah paling baru, tidak ada yang perlu diunduh.")
        return existing

    candles = fetch_ohlcv_paged(exchange, symbol, since_ms, now_ms)
    if not candles:
        print(f"  {symbol}: tidak ada candle baru.")
        return existing

    new_df = candles_to_dataframe(candles)
    print(f"  {symbol}: dapat {len(new_df)} candle baru "
          f"({new_df['timestamp'].min().date()} - {new_df['timestamp'].max().date()})")

    # Gabung dengan data lama, buang duplikat, simpan.
    combined = (pd.concat([existing, new_df], ignore_index=True)
                .drop_duplicates("timestamp")
                .sort_values("timestamp")
                .reset_index(drop=True))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(out_file, index=False)
    return combined


# ---------------------------------------------------------------------------
# Pemain utama
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Menyiapkan koneksi ke Binance lewat ccxt {ccxt.__version__}...")
    exchange = ccxt.binance({
        "enableRateLimit": True,   # ccxt patuh rate limit otomatis
        "options": {"defaultType": "spot"},
    })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for symbol in SYMBOLS:
        try:
            df = download_symbol(exchange, symbol)
            summary.append((symbol, len(df),
                            df["timestamp"].min() if len(df) else None,
                            df["timestamp"].max() if len(df) else None))
        except Exception as e:
            print(f"  {symbol}: GAGAL -> {e}")
            summary.append((symbol, 0, None, None))

    print("\n===== RINGKASAN =====")
    print(f"Folder: {OUTPUT_DIR}")
    for sym, n, lo, hi in summary:
        lo_s = lo.date() if lo is not None else "-"
        hi_s = hi.date() if hi is not None else "-"
        print(f"  {sym:10s}  {n:>5d} hari  {lo_s} -> {hi_s}")


if __name__ == "__main__":
    main()
