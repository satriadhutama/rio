"""
setup_jadwal.py — Pasang jadwal IDX Screening yang TAHAN BANTING ke Windows
Task Scheduler. Pakai schtasks.exe + XML definition (bawaan Windows, tanpa
install apa pun).

Setiap task otomatis dapat semua opsi resilient berikut:

  ✓ Run level: HIGHEST (privilege tinggi)
  ✓ Restart on failure: tiap 5 menit, maks 3x
  ✓ Run task ASAP setelah jadwal terlewat (internet putus / laptop mati)
  ✓ Wake the computer to run this task (laptop sleep -> bangun sendiri)
  ✓ Start only if network is available
  ✓ Execution time limit: 30 menit (force stop kalau lewat)

Jadwal:
  08:45  HARIAN -> IDX_Screening_0845
  10:00  HARIAN -> IDX_Screening_1000
  12:30  HARIAN -> IDX_Screening_1230
  15:30  HARIAN -> IDX_Screening_1530
  16:15  HARIAN -> IDX_Screening_1615
  09:00  JUMAT  -> IDX_Weekly_Report_Jumat

CARA PAKAI:
  python setup_jadwal.py            -> pasang/perbarui semua jadwal
  python setup_jadwal.py --status   -> daftar jadwal IDX_* yang aktif
  python setup_jadwal.py --info     -> Last Run / Next Run / Last Result per task
  python setup_jadwal.py --health   -> jalankan health_check.py
  python MATIKAN_JADWAL.py          -> hapus semua jadwal IDX_*

CATATAN ADMIN:
  Opsi "Wake the computer" & RunLevel=HIGHEST kadang minta hak admin.
  Kalau pertama kali `python setup_jadwal.py` muncul UAC prompt -> izinkan.
  Atau: klik kanan PowerShell -> Run as Administrator, baru jalankan script.
"""

import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_PATH = os.path.join(SCRIPT_DIR, "monitor_idx.py")
HEALTH_PATH = os.path.join(SCRIPT_DIR, "health_check.py")

# (nama_task, "HH:MM", weekly?)  -- weekly=True artinya Jumat saja
JADWAL = [
    ("IDX_Screening_0845",      "08:45", False),
    ("IDX_Screening_1000",      "10:00", False),
    ("IDX_Screening_1230",      "12:30", False),
    ("IDX_Screening_1530",      "15:30", False),
    ("IDX_Screening_1615",      "16:15", False),
    ("IDX_Weekly_Report_Jumat", "09:00", True),
]


# ---------------------------------------------------------------------------
# XML builder — generate satu Task Scheduler definition lengkap
# ---------------------------------------------------------------------------

def _trigger_xml(time_str: str, weekly: bool) -> str:
    hh, mm = time_str.split(":")
    start = f"2026-01-01T{hh}:{mm}:00"
    if weekly:
        return (f"    <CalendarTrigger>\n"
                f"      <StartBoundary>{start}</StartBoundary>\n"
                f"      <Enabled>true</Enabled>\n"
                f"      <ScheduleByWeek>\n"
                f"        <DaysOfWeek><Friday/></DaysOfWeek>\n"
                f"        <WeeksInterval>1</WeeksInterval>\n"
                f"      </ScheduleByWeek>\n"
                f"    </CalendarTrigger>")
    return (f"    <CalendarTrigger>\n"
            f"      <StartBoundary>{start}</StartBoundary>\n"
            f"      <Enabled>true</Enabled>\n"
            f"      <ScheduleByDay>\n"
            f"        <DaysInterval>1</DaysInterval>\n"
            f"      </ScheduleByDay>\n"
            f"    </CalendarTrigger>")


def build_task_xml(time_str: str, weekly: bool, script_path: str) -> str:
    """Generate XML untuk satu task dengan semua opsi resilient."""
    python_exe = escape(sys.executable)
    arg_path = escape(script_path)
    workdir = escape(SCRIPT_DIR)
    trigger = _trigger_xml(time_str, weekly)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo>\n'
        '    <Author>IDX Monitor</Author>\n'
        '    <Description>Screening Wyckoff/SMC top 100 saham IDX (otomatis, tahan banting).</Description>\n'
        '  </RegistrationInfo>\n'
        '  <Triggers>\n'
        f"{trigger}\n"
        '  </Triggers>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '      <RunLevel>HighestAvailable</RunLevel>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <AllowHardTerminate>true</AllowHardTerminate>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>\n'
        '    <IdleSettings>\n'
        '      <StopOnIdleEnd>true</StopOnIdleEnd>\n'
        '      <RestartOnIdle>false</RestartOnIdle>\n'
        '    </IdleSettings>\n'
        '    <AllowStartOnDemand>true</AllowStartOnDemand>\n'
        '    <Enabled>true</Enabled>\n'
        '    <Hidden>false</Hidden>\n'
        '    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n'
        '    <WakeToRun>true</WakeToRun>\n'
        '    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>\n'
        '    <Priority>7</Priority>\n'
        '    <RestartOnFailure>\n'
        '      <Interval>PT5M</Interval>\n'
        '      <Count>3</Count>\n'
        '    </RestartOnFailure>\n'
        '  </Settings>\n'
        '  <Actions Context="Author">\n'
        '    <Exec>\n'
        f"      <Command>{python_exe}</Command>\n"
        f'      <Arguments>"{arg_path}"</Arguments>\n'
        f"      <WorkingDirectory>{workdir}</WorkingDirectory>\n"
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
    )


def create_resilient_task(name: str, time_str: str, script_path: str,
                          weekly: bool = False) -> bool:
    """Buat / timpa satu task dari XML. schtasks /Create /XML /F = idempotent."""
    xml = build_task_xml(time_str, weekly, script_path)
    fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix=f"task_{name}_")
    os.close(fd)
    try:
        # Windows Task Scheduler /XML expects UTF-16 LE with BOM.
        with open(xml_path, "w", encoding="utf-16") as fh:
            fh.write(xml)
        result = subprocess.run(
            ["schtasks", "/Create", "/XML", xml_path, "/TN", name, "/F"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            sched = "WEEKLY Jumat" if weekly else "HARIAN"
            print(f"  ✅ {name:<28} -> {sched} {time_str}  (resilient)")
            return True
        err = (result.stderr or result.stdout or "").strip()
        print(f"  ❌ {name} GAGAL: {err}")
        return False
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Operasi user-facing
# ---------------------------------------------------------------------------

def setup_all():
    print("Memasang jadwal IDX Screening (resilient mode)...\n")
    sukses = 0
    for name, time_str, weekly in JADWAL:
        if create_resilient_task(name, time_str, MONITOR_PATH, weekly):
            sukses += 1

    total = len(JADWAL)
    print()
    if sukses == total:
        print(f"✅ {sukses}/{total} jadwal aktif (semua resilient)")
    else:
        print(f"⚠️ {sukses}/{total} jadwal aktif (ada yang gagal -- lihat pesan di atas)")
        print("   Coba: klik kanan PowerShell -> Run as Administrator, lalu ulangi.")

    print("\nOpsi resilient yang dipasang di tiap task:")
    print("  - Restart on failure: 5 menit x 3x percobaan")
    print("  - Run ASAP after missed start (StartWhenAvailable)")
    print("  - Wake the computer to run this task")
    print("  - Start only if network is available")
    print("  - RunLevel: HighestAvailable (privilege tinggi)")
    print("  - Stop if runs longer than 30 menit + force terminate")

    print("\nDaftar jadwal:")
    for name, time_str, weekly in JADWAL:
        ket = ("WEEKLY Jumat " if weekly else "HARIAN ") + time_str
        print(f"  - {name:<28} {ket}")

    print("\nLangkah berikutnya:")
    print("  python setup_jadwal.py --status   (lihat task aktif)")
    print("  python setup_jadwal.py --info     (Last/Next Run + Last Result)")
    print("  python setup_jadwal.py --health   (health check menyeluruh)")
    print("  python MATIKAN_JADWAL.py          (hapus semua kalau perlu)")


def _query_idx_tasks() -> list:
    """Ambil daftar nama task yang diawali IDX_Screening_ atau IDX_Weekly_."""
    result = subprocess.run(
        ["schtasks", "/Query", "/fo", "CSV", "/nh"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ Gagal membaca jadwal: {result.stderr.strip()}")
        return []
    tasks = []
    for line in result.stdout.splitlines():
        if "IDX_Screening_" in line or "IDX_Weekly_" in line:
            kolom = [c.strip('"') for c in line.split('","')]
            kolom[0] = kolom[0].lstrip('"')
            nama = kolom[0].lstrip("\\")
            if nama.startswith("IDX_Screening_") or nama.startswith("IDX_Weekly_"):
                tasks.append(nama)
    return sorted(set(tasks))


def show_status():
    print("Jadwal IDX_* yang aktif saat ini:\n")
    tasks = _query_idx_tasks()
    if not tasks:
        print("(belum ada). Jalankan: python setup_jadwal.py")
        return
    for t in tasks:
        print(f"  - {t}")
    print(f"\nTotal {len(tasks)} jadwal aktif.")


def show_info():
    print("Info Last Run / Next Run / Last Result per jadwal:\n")
    any_found = False
    for name, time_str, weekly in JADWAL:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ❌ {name}: tidak terpasang")
            continue
        any_found = True
        last_run = next_run = last_result = "(?)"
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "last run time":
                last_run = val
            elif key == "next run time":
                next_run = val
            elif key == "last result":
                last_result = val
        sched = "WEEKLY Jumat " + time_str if weekly else "HARIAN " + time_str
        print(f"  {name}   [{sched}]")
        print(f"    Last Run    : {last_run}")
        print(f"    Next Run    : {next_run}")
        print(f"    Last Result : {last_result}")
        print()
    if not any_found:
        print("(tidak ada task IDX_* terpasang -- jalankan: python setup_jadwal.py)")
    else:
        print("Catatan: 'Last Result' 0 atau 0x0 = sukses.")
        print("         1 = gagal (lihat trading_log.txt untuk detail).")


def show_health():
    """Delegate ke health_check.py supaya satu sumber kebenaran."""
    if not os.path.exists(HEALTH_PATH):
        print(f"❌ {HEALTH_PATH} tidak ditemukan.")
        print("   Pastikan health_check.py ada di folder yang sama dengan setup_jadwal.py.")
        return
    print(f"Menjalankan health check ({HEALTH_PATH})...\n")
    subprocess.run([sys.executable, HEALTH_PATH])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--info" in sys.argv:
        show_info()
    elif "--health" in sys.argv:
        show_health()
    else:
        setup_all()
