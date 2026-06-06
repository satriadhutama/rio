@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   IDX STOCK MONITOR
echo ============================================================
echo.

:: ── Cek apakah Python sudah terinstall ──────────────────────
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Python belum terinstall di komputer ini.
    echo.
    echo  Cara install Python ^(gratis, satu kali saja^):
    echo.
    echo    1. Buka browser, ketik: python.org/downloads
    echo    2. Klik tombol kuning besar "Download Python"
    echo    3. Jalankan file yang didownload
    echo    4. PENTING: centang kotak "Add Python to PATH"
    echo    5. Klik "Install Now" dan tunggu sampai selesai
    echo    6. Setelah selesai, klik dua kali file ini lagi
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] %PYVER% ditemukan.
echo.

:: ── Install / perbarui library ───────────────────────────────
echo  Mempersiapkan library yang dibutuhkan...
pip install yfinance pandas tabulate --quiet --upgrade
if %errorlevel% neq 0 (
    echo.
    echo  [!] Gagal install library. Pastikan ada koneksi internet.
    pause
    exit /b 1
)
echo  [OK] Semua library siap.
echo.

:: ── Jalankan script monitor ──────────────────────────────────
echo ============================================================
echo   Mengambil data saham IDX dan menghitung indikator...
echo ============================================================
echo.
python idx_monitor.py
if %errorlevel% neq 0 (
    echo.
    echo  [!] Ada masalah saat menjalankan script.
    echo      Pastikan koneksi internet aktif dan coba lagi.
    echo.
    pause
    exit /b 1
)

:: ── Selesai ──────────────────────────────────────────────────
echo.
echo ============================================================
echo   SELESAI!
echo   Hasil CSV tersimpan: idx_monitor_result.csv
echo   ^(ada di folder yang sama dengan file ini^)
echo ============================================================
echo.
set /p BUKA="  Buka hasil di Excel sekarang? Ketik y lalu Enter: "
if /i "%BUKA%"=="y" (
    echo.
    echo  Membuka Excel...
    start "" "idx_monitor_result.csv"
)

echo.
echo  Tekan tombol apa saja untuk tutup jendela ini.
pause > nul
