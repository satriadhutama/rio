# crypto-backtest

Backtest dan demo trading strategi trend-following daily untuk Bybit
perpetual. Pipeline-nya satu arah: data derivatif (OHLCV + funding rate
+ open interest) → indikator teknikal → sinyal entry → simulator
bar-per-bar dengan funding accounting → metrik kinerja → orchestrator
multi-coin.

## Strategi final: v2 (funding + OI filter)

Strategi produksi adalah `strategy_trend_v2.compute_signals_v2`, yaitu
strategi v1 (ADX + EMA bias + MACD cross) ditambah dua filter regime
berbasis data derivatif:

- **Funding rate ekstrem** memblok entry yang melawan kerumunan
  (long diblok saat funding 8h sangat positif; short diblok saat
  funding sangat negatif).
- **Open Interest growth** memblok entry saat OI ≤ rata-rata rolling
  (momentum tanpa partisipasi baru).

V1 (`strategy_trend.py`) dipertahankan sebagai baseline pembanding.
Eksperimen v3 (filter volatility) sudah dievaluasi dan didrop —
filter v3 menggugurkan terlalu banyak sinyal tanpa memperbaiki
risk-adjusted return secara meyakinkan.

## Universe

5 coin Bybit perpetual:

- BNB
- XRP
- ADA
- DOGE
- TRX

## Parameter strategi v2 (default)

| Parameter | Nilai | Catatan |
|---|---|---|
| `adx_threshold` | 20.0 | Regime: ADX > 20 = ada tren |
| `atr_sl_mult` | 2.0 | SL = entry ± 2 × ATR(14) |
| `atr_tp_mult` | 3.0 | TP = entry ± 3 × ATR(14) (R:R 1:1.5) |
| `funding_long_block` | 0.0003 | p90 historis funding 8h |
| `funding_short_block` | -0.00025 | p10 historis funding 8h |
| `oi_growth_min` | 0.0 | OI hari ini harus > rata-rata |
| `oi_growth_window` | 7 | Jendela rolling OI growth (hari) |

## Parameter backtest engine (default)

| Parameter | Nilai |
|---|---|
| `initial_capital` | USD 10.000 per coin (modal terpisah) |
| `risk_per_trade` | 3% ekuitas per trade |
| `leverage` | 10× notional |
| `fee_taker` | 0.055% per sisi |
| `slippage` | 0.05% adverse per sisi |
| `tie_break` | `SL` (worst-case saat SL & TP tersentuh di bar yang sama) |

## Cara update data

```bash
python deriv_data_layer.py --full
```

Mengunduh OHLCV harian, funding rate 8 jam, dan open interest harian
untuk semua coin universe, lalu menyimpannya ke
`data/deriv/{ohlcv,funding,oi}/{COIN}.parquet`. Mode inkremental: hanya
hari baru yang diunduh.

## Cara generate sinyal harian

```bash
python daily_signal.py
```

Membaca data terbaru, menjalankan pipeline v2, dan mencetak sinyal
entry untuk eksekusi hari ini (long/short/no-trade per coin dengan
SL/TP yang dihitung). _Catatan: file ini akan dibuat di iterasi
berikutnya._

## Modul

| File | Fungsi |
|---|---|
| `deriv_data_layer.py` | Downloader OHLCV + funding + OI dari Bybit (inkremental) |
| `indicators.py` | EMA, ATR, ADX, MACD via library `ta` |
| `strategy_trend.py` | Sinyal v1 (baseline) |
| `strategy_trend_v2.py` | Sinyal v2 (produksi) — v1 + funding/OI filter |
| `backtest_engine.py` | Simulator bar-per-bar dengan funding accounting |
| `metrics.py` | Statistik trade + rasio risiko/imbal hasil |
| `run_multi_coin.py` | Orchestrator backtest v1 di 5 coin |
| `run_multi_coin_v2.py` | Orchestrator backtest v2 di 5 coin |

## Status

- ✅ Backtest v1 & v2 selesai (data 2018–sekarang untuk coin yang ada).
- ✅ Strategi produksi terkunci di v2.
- 🟡 Sedang masuk fase **demo trading** (paper trade) sebelum live.
- ⬜ Live trading: pending dukungan eksekusi otomatis.

## Setup environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # isi kredensial API kalau perlu
```
