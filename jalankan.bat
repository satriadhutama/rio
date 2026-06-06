@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   IDX STOCK MONITOR
echo ============================================================
echo.

:: ── Cek Python ───────────────────────────────────────────────
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Python belum terinstall.
    echo.
    echo  Cara install ^(gratis, cukup sekali^):
    echo    1. Buka browser, ketik: python.org/downloads
    echo    2. Klik tombol kuning "Download Python"
    echo    3. Jalankan file yang didownload
    echo    4. PENTING: centang "Add Python to PATH"
    echo    5. Klik Install Now
    echo    6. Setelah selesai, klik dua kali file ini lagi
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PV=%%v
echo  [OK] %PV% ditemukan.
echo.

:: ── Install library ──────────────────────────────────────────
echo  Menginstall library yang dibutuhkan...
pip install yfinance pandas tabulate --quiet --upgrade
if %errorlevel% neq 0 (
    echo.
    echo  [!] Gagal install library. Pastikan internet aktif.
    pause
    exit /b 1
)
echo  [OK] Library siap.
echo.

:: ── Jalankan script ──────────────────────────────────────────
echo ============================================================
echo   Mengambil data dan menghitung indikator...
echo ============================================================
echo.
python monitor_idx.py
if %errorlevel% neq 0 (
    echo.
    echo  [!] Script berhenti karena error.
    echo      Pastikan koneksi internet aktif lalu coba lagi.
    echo.
    pause
    exit /b 1
)

:: ── Selesai ──────────────────────────────────────────────────
echo.
echo ============================================================
echo   SELESAI!
echo   File hasil: idx_hasil.csv  ^(bisa dibuka di Excel^)
echo ============================================================
echo.
set /p BUKA="  Buka idx_hasil.csv di Excel sekarang? (y/n): "
if /i "%BUKA%"=="y" start "" "idx_hasil.csv"

echo.
echo  Tekan tombol apa saja untuk tutup...
pause > nul
