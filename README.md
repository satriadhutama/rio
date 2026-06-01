# Quant Edge Engine — Crypto Trade Journal

Sistem pencatatan trade crypto berbasis Python + Excel (`.xlsx`).  
Satu file Excel, tiga sheet: **Log** (data trade), **Evaluasi** (metrik otomatis), **Equity** (kurva R + chart).

---

## Install

```bash
pip install openpyxl
```

---

## Cara Jalankan

### Mode A — Interaktif (menu di terminal)

```bash
python journal.py
```

Menu:
1. Catat call baru
2. Update hasil trade
3. Recompute evaluasi
4. Lihat ringkasan
0. Keluar

---

### Mode B — Satu-Baris Cepat

#### Catat call baru
```bash
python journal.py add "COIN,Setup,Bias,Score,Confidence,Verdict,Entry,SL,TP1,TP2,TP3,RR,P1,P2,P3,P4,P5,P6"
```

**Contoh:**
```bash
python journal.py add "SUI,Narrative,long,78,High,EKSEKUSI,3.85,3.77,4.0,4.2,4.5,1:3.5,1,1,1,1,1,0"
python journal.py add "BTC,Divergence,short,65,Med,WAIT,42000,42500,41000,,,1:2,1,0,-1,1,0,1"
```

- No & Tanggal otomatis
- Hasil default = `Pending`
- Field opsional di belakang (TP2, TP3, RR, P-) boleh dikosongkan

#### Update hasil trade
```bash
python journal.py result <No> "Hasil,R_aktual,Exit,catatan"
```

**Contoh:**
```bash
python journal.py result 1 "Win,3.0,4.5,TP3 kena bersih"
python journal.py result 2 "Loss,-1.0,42450,SL kena, invalidasi volume"
python journal.py result 3 "BE,0,41200,exit manual sebelum TP"
```

#### Lihat ringkasan statistik
```bash
python journal.py stats
```

#### Recompute manual (jika perlu)
```bash
python journal.py recompute
```

---

## Nilai yang Valid

| Field       | Nilai yang Diterima                              |
|-------------|--------------------------------------------------|
| Setup       | `Narrative`, `Divergence`, `SM pre-pump`, `Lain` |
| Bias        | `long`, `short`                                  |
| Confidence  | `Low`, `Med`, `High`                             |
| Verdict     | `EKSEKUSI`, `WAIT`, `ABAIKAN`                    |
| Hasil       | `Win`, `Loss`, `BE`, `Skip`, `Pending`           |
| P1–P6       | `-1`, `0`, `1`                                   |

---

## File Output

`quant_edge_journal.xlsx` — dibuat otomatis jika belum ada.

| Sheet    | Isi                                                         |
|----------|-------------------------------------------------------------|
| Log      | Semua trade, 1 baris per trade                              |
| Evaluasi | Metrik otomatis: expectancy, WR, per pilar/setup/verdict    |
| Equity   | Kurva R kumulatif + line chart                              |

---

## Catatan Metrik

- **Expectancy** = `(WinRate × AvgWin) − (LossRate × AvgLoss)`  
- Trade `Pending` dan `Skip` **tidak** dihitung di metrik
- Pilar dengan `n < 10` diberi tanda *(sampel kecil, belum reliabel)*
- Warna hijau = positif/prediktif, merah = negatif/lemah, kuning = sampel kecil
