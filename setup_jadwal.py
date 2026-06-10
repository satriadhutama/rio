"""
setup_jadwal.py — Pasang jadwal otomatis untuk monitor_idx.py via Windows
Task Scheduler (schtasks.exe). Tidak perlu install apa pun, semua bawaan
Windows.

Jadwal yang dipasang (waktu lokal komputer / WIB):
  08:45  setiap hari  -> IDX_Screening_0845
  10:00  setiap hari  -> IDX_Screening_1000
  12:30  setiap hari  -> IDX_Screening_1230
  15:30  setiap hari  -> IDX_Screening_1530
  16:15  setiap hari  -> IDX_Screening_1615
  09:00  setiap Jumat -> IDX_Screening_Weekly_0900  (persiapan laporan mingguan)

CARA PAKAI (jalankan dari Command Prompt / PowerShell, BUKAN double-click):
  python setup_jadwal.py            -> pasang/perbarui semua jadwal
  python setup_jadwal.py --status   -> lihat jadwal yang aktif sekarang

Aman dijalankan berulang kali — jadwal lama otomatis ditimpa (tidak dobel).
"""

import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_PATH = os.path.join(SCRIPT_DIR, "monitor_idx.py")

# (nama_task, jam "HH:MM", tipe_jadwal, hari_atau_None)
JADWAL = [
    ("IDX_Screening_0845", "08:45", "DAILY", None),
    ("IDX_Screening_1000", "10:00", "DAILY", None),
    ("IDX_Screening_1230", "12:30", "DAILY", None),
    ("IDX_Screening_1530", "15:30", "DAILY", None),
    ("IDX_Screening_1615", "16:15", "DAILY", None),
    ("IDX_Screening_Weekly_0900", "09:00", "WEEKLY", "FRI"),
]


def build_command() -> str:
    """Perintah yang dijalankan Task Scheduler: pindah ke folder proyek
    lalu jalankan monitor_idx.py dengan Python yang sedang dipakai."""
    return f'cmd /c cd /d "{SCRIPT_DIR}" && "{sys.executable}" "{MONITOR_PATH}"'


def create_task(name: str, time_str: str, sc_type: str, day: str = None) -> bool:
    cmd = [
        "schtasks", "/create",
        "/tn", name,
        "/tr", build_command(),
        "/sc", sc_type,
        "/st", time_str,
        "/f",  # timpa kalau sudah ada -> idempotent, tidak dobel
    ]
    if day:
        cmd += ["/d", day]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✅ {name} -> {sc_type} {time_str}" + (f" ({day})" if day else ""))
        return True
    else:
        print(f"  ❌ {name} GAGAL: {result.stderr.strip()}")
        return False


def setup_all():
    print("Memasang jadwal screening IDX otomatis...\n")
    sukses = 0
    for name, time_str, sc_type, day in JADWAL:
        if create_task(name, time_str, sc_type, day):
            sukses += 1

    total = len(JADWAL)
    print()
    if sukses == total:
        print(f"✅ {sukses}/{total} jadwal aktif")
    else:
        print(f"⚠️ {sukses}/{total} jadwal aktif (ada yang gagal, lihat pesan di atas)")

    print("\nDaftar jadwal:")
    for name, time_str, sc_type, day in JADWAL:
        ket = f"{sc_type} {time_str}" + (f" ({day})" if day else "")
        print(f"  - {name}: {ket}")

    print("\nCatatan:")
    print("  - Task akan menjalankan monitor_idx.py memakai Python yang sama")
    print("    dengan yang menjalankan setup_jadwal.py ini.")
    print("  - Pastikan TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID sudah diset")
    print("    permanen (lihat save_config.py) supaya alert Telegram terkirim")
    print("    walau dijalankan otomatis oleh Task Scheduler.")
    print("  - Cek jadwal kapan saja dengan: python setup_jadwal.py --status")
    print("  - Hapus semua jadwal dengan: python MATIKAN_JADWAL.py")


def show_status():
    print("Jadwal IDX_Screening_* yang aktif saat ini:\n")
    result = subprocess.run(
        ["schtasks", "/query", "/fo", "CSV", "/nh"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ Gagal membaca jadwal: {result.stderr.strip()}")
        return

    found = 0
    for line in result.stdout.splitlines():
        if "IDX_Screening_" in line:
            kolom = [c.strip('"') for c in line.split('","')]
            kolom[0] = kolom[0].lstrip('"')
            nama_task = kolom[0].lstrip("\\")
            info = ", ".join(kolom[1:]) if len(kolom) > 1 else ""
            print(f"  - {nama_task} ({info})")
            found += 1

    print()
    if found == 0:
        print("Belum ada jadwal IDX_Screening_* yang aktif.")
        print("Jalankan: python setup_jadwal.py")
    else:
        print(f"Total {found} jadwal aktif.")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    else:
        setup_all()
