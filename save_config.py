"""
save_config.py — Simpan TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID sebagai
ENVIRONMENT VARIABLE PERMANEN di akun Windows kamu (pakai `setx`, bawaan
Windows, tidak perlu install apa pun).

Kenapa perlu ini?
  Task Scheduler menjalankan monitor_idx.py di sesi baru yang TIDAK
  membaca variabel yang kamu set manual (`set` / `$env:` di terminal
  yang sedang terbuka). Dengan `setx`, nilainya ditulis permanen ke
  registry akun kamu (HKCU\\Environment) dan otomatis tersedia untuk
  semua proses baru, termasuk yang dijalankan Task Scheduler.

==========================================================================
PENTING — SEBELUM PAKAI:
  1. Buka https://t.me/BotFather, kirim /revoke untuk token Telegram LAMA
     (kalau token lama pernah ter-expose / dibagikan).
  2. Minta token BARU dari BotFather, lalu GANTI nilai BOT_TOKEN di bawah
     ini dengan token barumu (di antara tanda kutip).
  3. Baru jalankan script ini.

CATATAN KEAMANAN:
  `setx` menyimpan nilai dalam bentuk teks biasa (plaintext) di registry
  Windows (HKCU\\Environment). Siapa pun yang bisa login ke akun Windows
  kamu / punya akses registry bisa membacanya. Ini wajar untuk PC pribadi,
  tapi JANGAN pakai cara ini di komputer bersama / komputer kerja kantor.

CARA PAKAI:
  1. Edit BOT_TOKEN di bawah ini (ganti placeholder dengan token asli).
  2. Jalankan: python save_config.py
  3. BUKA JENDELA POWERSHELL/CMD BARU (yang lama tidak ikut ter-update),
     lalu cek dengan: python save_config.py --check
==========================================================================
"""

import os
import subprocess
import sys

# GANTI nilai di bawah ini dengan token BARU (setelah revoke token lama)
BOT_TOKEN = "GANTI_DENGAN_TOKEN_BARU_SETELAH_REVOKE"
CHAT_ID = "1674060319"


def setx(name: str, value: str) -> bool:
    result = subprocess.run(["setx", name, value], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✅ {name} tersimpan permanen")
        return True
    else:
        print(f"  ❌ gagal simpan {name}: {result.stderr.strip()}")
        return False


def verify_registry(name: str):
    """Verifikasi langsung dari registry (tidak perlu sesi baru)."""
    result = subprocess.run(
        ["reg", "query", "HKCU\\Environment", "/v", name],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith(name):
                print(f"  registry {name} = {line.split()[-1]}")
                return True
    print(f"  ❌ {name} tidak ditemukan di registry")
    return False


def check_session():
    """Verifikasi dari os.environ — hanya akurat di sesi terminal BARU."""
    print("Cek environment variable di sesi ini:\n")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if token:
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "(terlalu pendek)"
        print(f"  TELEGRAM_BOT_TOKEN = {masked}")
    else:
        print("  TELEGRAM_BOT_TOKEN = (kosong)")

    if chat_id:
        print(f"  TELEGRAM_CHAT_ID   = {chat_id}")
    else:
        print("  TELEGRAM_CHAT_ID   = (kosong)")

    if token and chat_id:
        print("\n✅ Konfigurasi lengkap. Scheduler siap jalan tanpa set manual.")
    else:
        print("\n⚠️ Belum lengkap. Jika baru saja menjalankan save_config.py,")
        print("   pastikan kamu membuka jendela PowerShell/CMD BARU lalu")
        print("   jalankan lagi: python save_config.py --check")


def main():
    if BOT_TOKEN.startswith("GANTI_DENGAN_TOKEN"):
        print("⚠️ PERINGATAN: BOT_TOKEN masih placeholder!")
        print("   Edit file save_config.py, ganti nilai BOT_TOKEN dengan")
        print("   token BARU dari @BotFather (setelah revoke token lama),")
        print("   baru jalankan ulang script ini.\n")
        print("   (Tetap melanjutkan menyimpan placeholder agar variabel")
        print("    sudah ada — tapi Telegram TIDAK akan bisa kirim sampai")
        print("    kamu ganti dengan token asli.)\n")

    print("Menyimpan konfigurasi ke environment variable permanen...\n")
    setx("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    setx("TELEGRAM_CHAT_ID", CHAT_ID)

    print("\nVerifikasi langsung dari registry:")
    verify_registry("TELEGRAM_BOT_TOKEN")
    verify_registry("TELEGRAM_CHAT_ID")

    print("\nToken tersimpan. Buka PowerShell BARU untuk verifikasi.")
    print("Mulai sekarang scheduler bisa jalan tanpa set manual.")
    print("(Verifikasi dari sesi baru: python save_config.py --check)")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_session()
    else:
        main()
