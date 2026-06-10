"""
health_check.py — Cek kesehatan sistem IDX Screening tanpa harus nungguin
jadwal berikutnya. Aman dijalankan kapan saja.

CARA PAKAI:
  python health_check.py

YANG DICEK:
  1. Internet                : online / offline
  2. Telegram bot            : token valid + bot bisa dihubungi
  3. Logging                 : kapan trading_log.txt terakhir diisi
  4. Scheduler               : berapa task IDX_* aktif sekarang
  5. Token                   : TELEGRAM_BOT_TOKEN tersedia di env

Setiap item ditandai ✅ (OK) atau ❌ (perlu diperbaiki) + penjelasan.

EXIT CODE:
  0 = semua OK
  1 = ada minimal satu masalah
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta

import requests

LOG_FILE = "trading_log.txt"
TELEGRAM_API = "https://api.telegram.org/bot{token}/getMe"


def check_internet() -> tuple:
    for url in ("https://www.google.com", "https://1.1.1.1"):
        try:
            requests.get(url, timeout=5)
            return True, "Online"
        except Exception:                                # noqa: BLE001
            continue
    return False, "OFFLINE (cek WiFi / modem)"


def check_telegram() -> tuple:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return False, "TELEGRAM_BOT_TOKEN belum di-set (jalankan save_config.py)"
    if token.startswith("GANTI_DENGAN_TOKEN") or token.startswith("["):
        return False, "Token masih placeholder, ganti dulu (revoke -> token baru -> save_config.py)"
    try:
        r = requests.get(TELEGRAM_API.format(token=token), timeout=8)
        if r.status_code == 200 and r.json().get("ok"):
            name = r.json().get("result", {}).get("username", "?")
            return True, f"Responsive (bot @{name})"
        if r.status_code == 401:
            return False, "TOKEN INVALID/REVOKED (minta token baru dari @BotFather)"
        return False, f"HTTP {r.status_code}: {r.text[:80]}"
    except requests.Timeout:
        return False, "TIMEOUT (cek token & internet)"
    except Exception as exc:                             # noqa: BLE001
        return False, f"ERROR ({exc})"


def _human_delta(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} detik lalu"
    if secs < 3600:
        return f"{secs // 60} menit lalu"
    if secs < 86400:
        return f"{secs // 3600} jam lalu"
    return f"{secs // 86400} hari lalu"


def check_logging() -> tuple:
    if not os.path.exists(LOG_FILE):
        return False, f"{LOG_FILE} belum ada (belum pernah ada eksekusi)"
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        if not lines:
            return False, f"{LOG_FILE} kosong"
        # cari timestamp pertama: format [YYYY-MM-DD HH:MM:SS] STATUS ...
        last_ts = None
        for ln in reversed(lines):
            if ln.startswith("[") and "]" in ln:
                ts_str = ln[1:ln.index("]")]
                try:
                    last_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    break
                except ValueError:
                    continue
        if last_ts is None:
            return False, "tidak bisa parse timestamp di log"
        delta = datetime.now() - last_ts
        when = _human_delta(delta)
        if delta > timedelta(hours=24):
            return False, f"Last entry {when} (>24 jam — scheduler mungkin mati)"
        return True, f"Last entry {when}"
    except Exception as exc:                             # noqa: BLE001
        return False, f"gagal baca log ({exc})"


def check_scheduler() -> tuple:
    result = subprocess.run(
        ["schtasks", "/Query", "/fo", "CSV", "/nh"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"schtasks error ({result.stderr.strip()[:80]})"
    names = set()
    for line in result.stdout.splitlines():
        if "IDX_Screening_" in line or "IDX_Weekly_" in line:
            kolom = [c.strip('"') for c in line.split('","')]
            kolom[0] = kolom[0].lstrip('"')
            nama = kolom[0].lstrip("\\")
            if nama.startswith("IDX_Screening_") or nama.startswith("IDX_Weekly_"):
                names.add(nama)
    count = len(names)
    if count == 0:
        return False, "Belum ada task IDX_* (jalankan setup_jadwal.py)"
    if count < 6:
        return False, f"Hanya {count} task aktif (harusnya 6). Cek setup_jadwal.py --status."
    return True, f"{count} active tasks"


def check_token() -> tuple:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        return False, "TELEGRAM_BOT_TOKEN tidak ditemukan di env (jalankan save_config.py)"
    if token.startswith("GANTI_DENGAN_TOKEN") or token.startswith("["):
        return False, "Token masih placeholder"
    if not chat:
        return False, "TELEGRAM_BOT_TOKEN OK, tapi TELEGRAM_CHAT_ID kosong"
    return True, "Configured"


CHECKS = [
    ("Internet      ", check_internet),
    ("Telegram bot  ", check_telegram),
    ("Logging       ", check_logging),
    ("Scheduler     ", check_scheduler),
    ("Token         ", check_token),
]


def main():
    print("=== HEALTH CHECK ===")
    all_ok = True
    for label, fn in CHECKS:
        try:
            ok, msg = fn()
        except Exception as exc:                         # noqa: BLE001
            ok, msg = False, f"check error: {exc}"
        mark = "✅" if ok else "❌"
        print(f"{mark} {label}: {msg}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("Status keseluruhan: SEHAT — siap auto-screening.")
        sys.exit(0)
    else:
        print("Status keseluruhan: PERLU PERHATIAN — perbaiki item bertanda ❌ di atas.")
        sys.exit(1)


if __name__ == "__main__":
    main()
