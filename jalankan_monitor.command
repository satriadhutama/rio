#!/bin/bash
# File ini untuk pengguna macOS. Klik dua kali untuk menjalankan.

# Pindah ke folder tempat file ini berada
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  IDX STOCK MONITOR"
echo "============================================================"
echo ""

# ── Cek Python ───────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo " [!] Python3 belum terinstall."
    echo ""
    echo " Cara install di macOS:"
    echo "   1. Buka browser, ketik: python.org/downloads"
    echo "   2. Download dan jalankan installer untuk macOS"
    echo "   3. Setelah selesai, klik dua kali file ini lagi"
    echo ""
    read -p " Tekan Enter untuk tutup..."
    exit 1
fi

echo " [OK] $(python3 --version) ditemukan."
echo ""

# ── Install library ──────────────────────────────────────────
echo " Mempersiapkan library yang dibutuhkan..."
pip3 install yfinance pandas tabulate --quiet --upgrade
if [ $? -ne 0 ]; then
    echo ""
    echo " [!] Gagal install library. Pastikan ada koneksi internet."
    read -p " Tekan Enter untuk tutup..."
    exit 1
fi
echo " [OK] Semua library siap."
echo ""

# ── Jalankan script ──────────────────────────────────────────
echo "============================================================"
echo "  Mengambil data saham IDX dan menghitung indikator..."
echo "============================================================"
echo ""
python3 idx_monitor.py
if [ $? -ne 0 ]; then
    echo ""
    echo " [!] Ada masalah. Pastikan koneksi internet aktif."
    read -p " Tekan Enter untuk tutup..."
    exit 1
fi

# ── Selesai ──────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  SELESAI!"
echo "  Hasil CSV tersimpan: idx_monitor_result.csv"
echo "============================================================"
echo ""
read -p "  Buka hasil di Excel sekarang? (y/n): " BUKA
if [[ "$BUKA" == "y" || "$BUKA" == "Y" ]]; then
    open "idx_monitor_result.csv"
fi

echo ""
read -p "  Tekan Enter untuk tutup jendela ini."
