"""
deriv_data_layer.py
===================

Tarik data historis derivatif (perpetual linear USDT) dari Bybit untuk
universe big-cap statis. Semua data via endpoint PUBLIK ccxt.bybit() —
TANPA API key.

Tiga jenis data per coin:
  1. OHLCV harian          -> data/deriv/ohlcv/{KEY}.parquet
  2. Funding rate (8 jam)  -> data/deriv/funding/{KEY}.parquet  (di-resample harian)
  3. Open Interest harian  -> data/deriv/oi/{KEY}.parquet

Prinsip:
  - Tidak ada data dummy/synthetic. Semua dari API Bybit.
  - Tidak ada try/except kosong. Retry HANYA untuk error jaringan transient;
    error lain (mis. BadSymbol) di-raise apa adanya.
  - Jeda 0.3s antar request, retry 3x (sleep 5s) untuk network error.
  - Checkpoint: file yang sudah ada di-skip (resume-friendly).
  - Logging jelas: jumlah baris, rentang tanggal, path per coin per jenis.

CLI:
  python deriv_data_layer.py --test   # hanya BTC (default)
  python deriv_data_layer.py --full   # semua 10 coin
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import ccxt
import pandas as pd

# --------------------------------------------------------------------------- #
# Konfigurasi
# --------------------------------------------------------------------------- #

SYMBOLS = {
    'BTC': 'BTC/USDT:USDT', 'ETH': 'ETH/USDT:USDT', 'SOL': 'SOL/USDT:USDT',
    'BNB': 'BNB/USDT:USDT', 'XRP': 'XRP/USDT:USDT', 'ADA': 'ADA/USDT:USDT',
    'DOGE': 'DOGE/USDT:USDT', 'AVAX': 'AVAX/USDT:USDT', 'LINK': 'LINK/USDT:USDT',
    'TRX': 'TRX/USDT:USDT',
}

# Titik awal pagination maju (OHLCV & funding). Bybit otomatis membalas dari
# tanggal listing jika perp baru listing belakangan -> tidak crash.
START_DATE_MS = ccxt.bybit().parse8601('2020-01-01T00:00:00Z')

DATA_ROOT = Path('data') / 'deriv'
OHLCV_DIR = DATA_ROOT / 'ohlcv'
FUNDING_DIR = DATA_ROOT / 'funding'
OI_DIR = DATA_ROOT / 'oi'

RATE_SLEEP = 0.3        # jeda antar request (Bybit 120/menit -> aman)
RETRY_MAX = 3           # percobaan ulang untuk network error transient
RETRY_SLEEP = 5         # detik antar percobaan ulang

ONE_DAY_MS = 24 * 60 * 60 * 1000

# Skema kolom (dipakai juga untuk membuat file kosong yang valid)
OHLCV_COLS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
FUNDING_RAW_COLS = ['timestamp', 'funding_rate']
FUNDING_DAILY_COLS = ['date', 'funding_rate_daily_sum', 'funding_rate_8h_mean']
OI_COLS = ['date', 'open_interest_usd']

# Error jaringan transient yang layak di-retry (BUKAN error logika/symbol)
TRANSIENT_ERRORS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.ExchangeNotAvailable,
)

log = logging.getLogger('deriv_data_layer')


# --------------------------------------------------------------------------- #
# Utilitas
# --------------------------------------------------------------------------- #

def make_exchange() -> ccxt.bybit:
    """ccxt.bybit endpoint publik, default ke pasar swap (perpetual)."""
    ex = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })
    ex.load_markets()
    return ex


def with_retry(fn, *args, **kwargs):
    """Panggil fn dengan retry HANYA untuk error jaringan transient.

    Error non-transient (mis. BadSymbol, ArgumentsRequired) langsung di-raise
    supaya tidak ditelan diam-diam.
    """
    last_exc = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            return fn(*args, **kwargs)
        except TRANSIENT_ERRORS as exc:
            last_exc = exc
            log.warning('  network error (percobaan %d/%d): %s -> sleep %ds',
                        attempt, RETRY_MAX, type(exc).__name__, RETRY_SLEEP)
            if attempt < RETRY_MAX:
                time.sleep(RETRY_SLEEP)
    # Habis semua percobaan -> naikkan error terakhir, jangan ditelan.
    raise last_exc


def _date_span(series: pd.Series) -> str:
    """String rentang tanggal untuk logging."""
    if series.empty:
        return '(kosong)'
    return f'{series.min()} .. {series.max()}'


def save_parquet(df: pd.DataFrame, path: Path, key: str, label: str,
                 date_col: str) -> None:
    """Tulis parquet + logging detail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info('  [%s %s] %d baris | %s | -> %s',
             key, label, len(df), _date_span(df[date_col]) if date_col in df else '(?)',
             path)


# --------------------------------------------------------------------------- #
# 1. OHLCV harian
# --------------------------------------------------------------------------- #

def fetch_ohlcv_full(exchange: ccxt.bybit, symbol: str, key: str) -> pd.DataFrame:
    """Pagination MAJU dari START_DATE sampai sekarang.

    Bybit membalas mulai dari tanggal listing kalau perp baru listing belakangan.
    """
    rows: list[list] = []
    cursor = START_DATE_MS
    now_ms = exchange.milliseconds()

    while cursor < now_ms:
        batch = with_retry(exchange.fetch_ohlcv, symbol, '1d', cursor, 1000)
        time.sleep(RATE_SLEEP)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        # Maju 1 hari dari candle terakhir agar tidak mengambil ulang.
        next_cursor = last_ts + ONE_DAY_MS
        if next_cursor <= cursor:        # pengaman anti-loop
            break
        cursor = next_cursor
        if len(batch) < 1000:            # batch terakhir
            break

    if not rows:
        return pd.DataFrame(columns=OHLCV_COLS)

    df = pd.DataFrame(rows, columns=OHLCV_COLS)
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp')
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 2. Funding rate historis (8 jam) -> resample harian
# --------------------------------------------------------------------------- #

def fetch_funding_full(exchange: ccxt.bybit, symbol: str, key: str) -> pd.DataFrame:
    """Pagination MUNDUR funding rate (Bybit funding tiap 8 jam) sampai habis,
    lalu resample ke harian (sum 3 funding + mean).

    Catatan: Bybit fetch_funding_rate_history tidak melayani `since` jauh ke
    belakang (mis. 2020) -> endpoint membalas window MUNDUR dari titik acuan.
    Karena itu kita mulai dari sekarang (since=None) dan geser mundur via
    params={'until': <timestamp>}, mirip pola fetch_oi_full.
    """
    records: list[dict] = []
    until = None  # None = ambil batch paling baru lebih dulu
    seen_oldest = None

    while True:
        params = {} if until is None else {'until': until}
        batch = with_retry(exchange.fetch_funding_rate_history,
                           symbol, None, 200, params)
        time.sleep(RATE_SLEEP)
        if not batch:
            break
        records.extend(batch)
        oldest_ts = min(r['timestamp'] for r in batch)
        if seen_oldest is not None and oldest_ts >= seen_oldest:
            break  # tidak maju mundur lagi -> stop
        seen_oldest = oldest_ts
        if oldest_ts <= START_DATE_MS:
            break
        until = oldest_ts - 1
        if len(batch) < 200:             # batch terakhir
            break

    if not records:
        return pd.DataFrame(columns=FUNDING_DAILY_COLS)

    raw = pd.DataFrame({
        'timestamp': [r['timestamp'] for r in records],
        'funding_rate': [r['fundingRate'] for r in records],
    })
    raw = raw.dropna(subset=['timestamp'])
    raw = raw.drop_duplicates(subset='timestamp').sort_values('timestamp')
    raw['timestamp'] = pd.to_datetime(raw['timestamp'], unit='ms', utc=True)

    # Resample harian: jumlah (sum ~3x8jam) dan rata-rata.
    raw['date'] = raw['timestamp'].dt.normalize()
    daily = (raw.groupby('date')['funding_rate']
                .agg(funding_rate_daily_sum='sum', funding_rate_8h_mean='mean')
                .reset_index())
    return daily[FUNDING_DAILY_COLS]


# --------------------------------------------------------------------------- #
# 3. Open Interest historis
# --------------------------------------------------------------------------- #

def fetch_oi_full(exchange: ccxt.bybit, symbol: str, key: str) -> pd.DataFrame:
    """OI harian. Bybit hanya menyimpan window OI terbatas, jadi kita
    paginate MUNDUR dari sekarang sampai data habis.

    Jika method tidak didukung -> log dan kembalikan DataFrame kosong (tidak crash).
    """
    if not exchange.has.get('fetchOpenInterestHistory'):
        log.warning('  [%s OI] fetchOpenInterestHistory tidak didukung ccxt bybit '
                    '-> tulis file kosong', key)
        return pd.DataFrame(columns=OI_COLS)

    records: list[dict] = []
    until = None  # None = ambil batch paling baru lebih dulu
    seen_oldest = None

    while True:
        try:
            params = {} if until is None else {'until': until}
            batch = with_retry(exchange.fetch_open_interest_history,
                               symbol, '1d', None, 200, params)
        except ccxt.NotSupported as exc:
            log.warning('  [%s OI] tidak tersedia (%s) -> tulis file kosong',
                        key, exc)
            return pd.DataFrame(columns=OI_COLS)
        time.sleep(RATE_SLEEP)

        if not batch:
            break
        records.extend(batch)
        oldest_ts = min(r['timestamp'] for r in batch)
        if seen_oldest is not None and oldest_ts >= seen_oldest:
            break  # tidak maju mundur lagi -> stop
        seen_oldest = oldest_ts
        if oldest_ts <= START_DATE_MS:
            break
        until = oldest_ts - 1
        if len(batch) < 200:
            break

    if not records:
        log.info('  [%s OI] API tidak mengembalikan data OI', key)
        return pd.DataFrame(columns=OI_COLS)

    # Bybit linear: openInterestValue (USD) bila ada; fallback amount (kontrak).
    def _val(r):
        v = r.get('openInterestValue')
        return v if v is not None else r.get('openInterestAmount')

    oi = pd.DataFrame({
        'date': [r['timestamp'] for r in records],
        'open_interest_usd': [_val(r) for r in records],
    })
    oi = oi.dropna(subset=['date']).drop_duplicates(subset='date').sort_values('date')
    oi['date'] = pd.to_datetime(oi['date'], unit='ms', utc=True).dt.normalize()
    return oi.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Orkestrasi per coin
# --------------------------------------------------------------------------- #

def process_coin(exchange: ccxt.bybit, key: str, symbol: str) -> None:
    log.info('=== %s (%s) ===', key, symbol)

    # 1. OHLCV
    ohlcv_path = OHLCV_DIR / f'{key}.parquet'
    if ohlcv_path.exists():
        log.info('  [%s OHLCV] checkpoint ada -> skip (%s)', key, ohlcv_path)
    else:
        df = fetch_ohlcv_full(exchange, symbol, key)
        save_parquet(df, ohlcv_path, key, 'OHLCV', 'timestamp')

    # 2. Funding
    funding_path = FUNDING_DIR / f'{key}.parquet'
    if funding_path.exists():
        log.info('  [%s FUNDING] checkpoint ada -> skip (%s)', key, funding_path)
    else:
        df = fetch_funding_full(exchange, symbol, key)
        save_parquet(df, funding_path, key, 'FUNDING', 'date')

    # 3. Open Interest
    oi_path = OI_DIR / f'{key}.parquet'
    if oi_path.exists():
        log.info('  [%s OI] checkpoint ada -> skip (%s)', key, oi_path)
    else:
        df = fetch_oi_full(exchange, symbol, key)
        save_parquet(df, oi_path, key, 'OI', 'date')


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument('--test', action='store_true',
                   help='Hanya BTC (default).')
    g.add_argument('--full', action='store_true',
                   help='Semua 10 coin.')
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    args = parse_args()

    if args.full:
        selected = dict(SYMBOLS)
        mode = 'FULL (10 coin)'
    else:
        selected = {'BTC': SYMBOLS['BTC']}
        mode = 'TEST (BTC saja)'

    log.info('Mode: %s', mode)
    exchange = make_exchange()

    for key, symbol in selected.items():
        process_coin(exchange, key, symbol)

    log.info('Selesai.')


if __name__ == '__main__':
    main()
