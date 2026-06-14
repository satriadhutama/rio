# Demo Trading Workflow — Strategi v2

## Tujuan

Validasi strategi v2 di Bybit testnet sebelum naik ke uang riil. Demo
dijalankan dengan disiplin penuh — eksekusi konsisten, pencatatan
lengkap, evaluasi statistik berbasis sample.

## Batasan demo (jujur soal trade-off)

Bybit testnet **tidak identik** dengan market riil:

- Slippage testnet sering lebih optimis dari realita.
- Funding rate kadang outdated.
- Liquidity book sintetis.

Jadi: hasil demo positif = **syarat**, bukan **jaminan**. Hasil
negatif = sinyal jelas untuk stop dan re-evaluasi sebelum live.

## Setup akun demo

1. Buka akun di [testnet.bybit.com](https://testnet.bybit.com).
2. Modal demo: **$2000 USDT per coin × 5 coin = $10.000 total**
   (mirror backtest).
3. Set leverage: **10× untuk semua 5 coin** (perpetual USDT).
4. Mode: **One-way** (bukan hedge mode).

## Rutinitas harian (5–10 menit per hari)

### Pagi (sebelum 06:55 WIB / 23:55 UTC)

1. `python deriv_data_layer.py --full` — update data sampai bar kemarin.
2. `python daily_signal.py` — lihat sinyal hari ini.
3. Catat sinyal aktif di jurnal (Excel / Notion).

### Saat 07:00 WIB (00:00 UTC, bar daily baru terbentuk)

4. Buka Bybit testnet.
5. Untuk setiap sinyal aktif (status `TRADE LONG` / `TRADE SHORT`):
   - Place **MARKET order** di harga open bar baru.
   - Set **SL = entry − 2·ATR** (long) atau **entry + 2·ATR** (short).
   - Set **TP = entry + 3·ATR** (long) atau **entry − 3·ATR** (short).
   - Position size: **risk 3% × $2000 = $60 risk per trade per coin**.
6. Posisi auto-close oleh SL/TP — **tidak ada intervensi manual**.

### Aturan disiplin (NON-NEGOTIABLE)

- **JANGAN** skip sinyal karena "kelihatan rugi" — itu cherry-picking
  yang merusak validasi.
- **JANGAN** tambah trade di luar sinyal — strategi yang divalidasi
  cuma yang dari script.
- **JANGAN** ubah SL/TP setelah entry.
- **JANGAN** intervensi posisi yang lagi rugi ("close cepat") atau
  lagi untung ("biarkan lari").
- Kalau eksekusi salah (entry telat, SL salah set), catat di kolom
  `execution_error` — itu data berharga, bukan kesalahan untuk
  ditutupi.

## Jurnal trading

Kolom wajib per trade (Excel atau pandas DataFrame):

| Kolom | Isi |
|---|---|
| `entry_date` | Tanggal entry |
| `coin` | BNB / XRP / ADA / DOGE / TRX |
| `side` | long / short |
| `signal_entry_price` | Harga yang ditampilkan `daily_signal.py` |
| `actual_entry_price` | Harga eksekusi sebenarnya di Bybit |
| `slippage_pct` | (actual − signal) / signal × 100 |
| `sl_price` | Sesuai sinyal |
| `tp_price` | Sesuai sinyal |
| `exit_date` | Tanggal exit |
| `exit_price` | Harga exit |
| `exit_reason` | SL / TP / manual |
| `gross_pnl_usd` | (exit − entry) × size, sesuai arah |
| `fees_usd` | Total fee (entry + exit) |
| `funding_usd` | Akumulasi funding selama posisi terbuka |
| `net_pnl_usd` | gross − fees − funding |
| `execution_error` | Catatan delay / mis-execution / salah size (kosong kalau bersih) |

## Evaluasi (mandatory checkpoint)

### Checkpoint 1 — setelah 30 trade demo

Hitung metrik realisasi pakai script yang sama (`metrics.py`):

- Win rate realisasi vs backtest v2.
- Profit factor realisasi vs backtest.
- Slippage rata-rata.
- Konsistensi eksekusi: ada `execution_error` berapa kali.

**Tolak go-live kalau:**

- Win rate realisasi > 15 percentage point di bawah backtest
  (signal degradation).
- Profit factor realisasi < 0.9 (strategi rugi di market riil-ish).
- Lebih dari 5 trade `execution_error` (operator belum disiplin).

Kalau salah satu di atas terjadi: **stop demo, diagnosa, perbaiki,
ulangi dari trade ke-1.** Bukan lanjut dengan harapan "rata-rata
akan membaik".

### Checkpoint 2 — setelah 60 trade demo

Ini gate terakhir sebelum naik ke uang riil. Semua syarat berikut
harus terpenuhi BERSAMAAN — bukan rata-rata.

**Syarat go-live (uang riil):**

- ✅ **60+ trade demo selesai** dengan eksekusi disiplin (mengulang
  dari Checkpoint 1 tidak masalah; yang dihitung adalah 60 trade
  TERAKHIR setelah perbaikan terakhir).
- ✅ **Sharpe demo > 0** — strategi memberi return positif relatif
  terhadap volatilitas.
- ✅ **Profit factor demo > 1.1** — total profit minimal 1.1× total
  loss.
- ✅ **Max drawdown demo tidak melebihi 1.5× max drawdown backtest**
  — kalau backtest max DD −20%, demo tidak boleh tembus −30%.
- ✅ **Psikologis OK** dengan drawdown yang terjadi — Anda bisa tidur
  malam saat sedang DD, dan tidak tergoda intervensi. Kalau jawabannya
  ragu, **belum siap live**.

Salah satu syarat gagal = stop, perpanjang demo, atau perbaiki
strategi/eksekusi. Tidak ada "nyeberang sedikit-sedikit".

## Setelah go-live (untuk referensi)

- Mulai dengan **modal kecil** (mis. 25% target alokasi) selama 30
  trade pertama uang riil — masih babak validasi.
- Jurnal trading lanjut, kolom sama persis.
- Re-evaluasi tiap 30 trade. Drop ke demo kalau metrik live menyimpang
  dari ekspektasi.
- Strategi bukan static asset — pasar berubah, parameter mungkin perlu
  re-tune setiap 6–12 bulan dengan disiplin yang sama (walk-forward
  test, bukan curve fitting).

## Catatan untuk diri sendiri

Demo trading **bukan** "main-main sambil nunggu live". Demo adalah
sample statistik yang Anda kumpulkan untuk membuat satu keputusan
penting: lanjut ke uang riil atau tidak. Setiap trade demo yang
diskip / di-cherry-pick / di-intervensi membuat sample itu kotor
dan keputusan akhirnya jadi tebakan, bukan kesimpulan.

Disiplin di demo = disiplin di live. Disiplin yang baru muncul di
live adalah disiplin yang tidak ada.
