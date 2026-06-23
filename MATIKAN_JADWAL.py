"""
MATIKAN_JADWAL.py — Tombol darurat: hapus SEMUA jadwal screening IDX
(IDX_Screening_* dan IDX_Weekly_Report_*) dari Windows Task Scheduler.

Pakai ini kalau:
  - mau berhenti sementara (misal lagi cuti / pasar tutup lama)
  - mau pasang ulang jadwal dari awal (jalankan ini dulu, baru
    setup_jadwal.py lagi)
  - ada masalah dan ingin "matikan semua dulu" sebelum cek lebih lanjut

CARA PAKAI:
  python MATIKAN_JADWAL.py

Tidak akan menghapus jadwal lain di komputer kamu — hanya yang namanya
diawali "IDX_Screening_" atau "IDX_Weekly_".
"""

import subprocess


def list_idx_tasks():
    result = subprocess.run(
        ["schtasks", "/query", "/fo", "CSV", "/nh"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ Gagal membaca daftar jadwal: {result.stderr.strip()}")
        return []

    tasks = []
    for line in result.stdout.splitlines():
        if "IDX_Screening_" in line or "IDX_Weekly_" in line:
            kolom = [c.strip('"') for c in line.split('","')]
            kolom[0] = kolom[0].lstrip('"')
            nama_task = kolom[0].lstrip("\\")
            if (nama_task.startswith("IDX_Screening_")
                    or nama_task.startswith("IDX_Weekly_")):
                tasks.append(nama_task)
    return sorted(set(tasks))


def delete_task(name: str) -> bool:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", name, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  🗑️  {name} dihapus")
        return True
    else:
        print(f"  ❌ {name} gagal dihapus: {result.stderr.strip()}")
        return False


def main():
    print("Mencari jadwal IDX_Screening_*...\n")
    tasks = list_idx_tasks()

    if not tasks:
        print("Tidak ada jadwal IDX_Screening_* yang aktif. Tidak ada yang dihapus.")
        return

    print(f"Ditemukan {len(tasks)} jadwal:")
    for t in tasks:
        print(f"  - {t}")
    print()

    dihapus = 0
    for t in tasks:
        if delete_task(t):
            dihapus += 1

    print()
    print(f"🛑 {dihapus}/{len(tasks)} jadwal dihapus")
    if dihapus == len(tasks):
        print("Semua jadwal screening otomatis sudah MATI.")
        print("Untuk pasang lagi: python setup_jadwal.py")


if __name__ == "__main__":
    main()
