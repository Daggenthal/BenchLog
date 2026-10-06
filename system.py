"""Host health, admin password, background jobs, backups, and update helpers.

Nothing in here needs Flask. app.py calls configure() once at startup.
"""

import glob
import hashlib
import hmac
import json
import os
import platform
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "benchlog.db")
STARTED_AT = time.time()

KEEP_AUTO = 14      # daily backups kept
KEEP_OTHER = 10     # pre-update, pre-restore, and manual backups kept, per kind
BACKUP_NAME = re.compile(r"^[a-z-]+-\d{8}-\d{6}\.db$")


def configure(data_dir, db_path):
    global DATA_DIR, DB_PATH
    DATA_DIR, DB_PATH = data_dir, db_path
    for sub in ("backups", "logs"):
        os.makedirs(os.path.join(DATA_DIR, sub), exist_ok=True)


def _read(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def _run(cmd, timeout=10, **kwargs):
    """Run a short command and return (exit code, combined output). Never raises."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=timeout, **kwargs)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def load_json(name, default=None):
    try:
        with open(os.path.join(DATA_DIR, name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(name, value, private=False):
    path = os.path.join(DATA_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(value, fh, indent=1)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ------------------------------------------------------------------- health

def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def human_duration(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return "%dd %dh %dm" % (d, h, m)
    if h:
        return "%dh %dm" % (h, m)
    return "%dm" % m


def _cpu_times():
    parts = _read("/proc/stat").splitlines()[0].split()[1:]
    values = [int(x) for x in parts]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent(sample=0.2):
    try:
        idle1, total1 = _cpu_times()
        time.sleep(sample)
        idle2, total2 = _cpu_times()
        span = total2 - total1
        return round(100.0 * (1 - (idle2 - idle1) / span), 1) if span > 0 else 0.0
    except (IndexError, ValueError):
        return None


def meminfo():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    return info


def dir_size(path):
    total, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
    return total, count


THROTTLE_BITS = [
    (0, "Under-voltage right now. Check the power supply"),
    (1, "CPU frequency capped right now"),
    (2, "Throttled right now"),
    (3, "Soft temperature limit active right now"),
    (16, "Under-voltage has happened since boot"),
    (17, "Frequency capping has happened since boot"),
    (18, "Throttling has happened since boot"),
    (19, "Soft temperature limit was reached since boot"),
]


def decode_throttled(text):
    """Turn 'throttled=0x50005' from vcgencmd into plain warnings."""
    match = re.search(r"0x([0-9a-fA-F]+)", text or "")
    if not match:
        return None
    value = int(match.group(1), 16)
    return [message for bit, message in THROTTLE_BITS if value & (1 << bit)]


def gpu_info():
    gpus = []
    for path in sorted(glob.glob("/sys/class/drm/card[0-9]*/device/gpu_busy_percent")):
        base = os.path.dirname(path)
        entry = {"name": os.path.basename(os.path.dirname(base)), "busy": None, "vram": None}
        try:
            entry["busy"] = float(_read(path))
        except ValueError:
            pass
        used, total = _read(os.path.join(base, "mem_info_vram_used")), _read(os.path.join(base, "mem_info_vram_total"))
        if used.isdigit() and total.isdigit() and int(total):
            entry["vram"] = (int(used), int(total))
        gpus.append(entry)
    return gpus


def ip_addresses():
    rc, out = _run(["hostname", "-I"], timeout=3)
    if rc == 0 and out:
        return [a for a in out.split() if ":" not in a] or out.split()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        address = s.getsockname()[0]
        s.close()
        return [address]
    except OSError:
        return []


def read_health():
    """One snapshot of the machine the app is running on."""
    h = {"hostname": socket.gethostname(), "ips": ip_addresses(), "kernel": platform.release(),
         "python": platform.python_version(), "arch": platform.machine()}
    h["model"] = (_read("/proc/device-tree/model").replace("\x00", "")
                  or _read("/sys/devices/virtual/dmi/id/product_name") or "Unknown")
    os_name = ""
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    h["os"] = os_name or platform.system()

    try:
        uptime = float(_read("/proc/uptime").split()[0])
    except (IndexError, ValueError):
        uptime = 0
    h["uptime"] = human_duration(uptime)
    h["booted_at"] = int(time.time() - uptime)
    h["app_uptime"] = human_duration(time.time() - STARTED_AT)

    h["cpu_percent"] = cpu_percent()
    h["cpu_count"] = os.cpu_count() or 1
    load = _read("/proc/loadavg").split()[:3]
    h["load"] = [float(x) for x in load] if len(load) == 3 else []
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    h["cpu_temp"] = round(int(temp) / 1000.0, 1) if temp.lstrip("-").isdigit() else None

    mem = meminfo()
    total, available = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    h["mem"] = {"total": total, "used": total - available,
                "percent": round(100.0 * (total - available) / total, 1) if total else 0}
    swap_total, swap_free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    h["swap"] = {"total": swap_total, "used": swap_total - swap_free,
                 "percent": round(100.0 * (swap_total - swap_free) / swap_total, 1) if swap_total else 0}

    disks, seen = [], set()
    for label, path in (("App data", DATA_DIR), ("System", "/")):
        try:
            usage = shutil.disk_usage(path)
            device = os.stat(path).st_dev
        except OSError:
            continue
        if device in seen:
            disks[0]["label"] = "System and app data"
            continue
        seen.add(device)
        # "Used" counts everything that is not free to this user, including space the
        # filesystem reserves, so used and free always add up to the total.
        free_percent = round(100.0 * usage.free / usage.total, 1) if usage.total else 0
        disks.append({"label": label, "total": usage.total, "used": usage.total - usage.free,
                      "free": usage.free, "percent": round(100 - free_percent, 1), "free_percent": free_percent})
    h["disks"] = disks
    rc, source = _run(["findmnt", "-n", "-o", "SOURCE", "/"], timeout=3)
    h["root_device"] = source if rc == 0 else ""
    h["on_sd_card"] = "mmcblk" in h["root_device"]

    h["gpus"] = gpu_info()
    h["throttled"] = None
    h["gpu_temp"] = None
    if shutil.which("vcgencmd"):
        rc, out = _run(["vcgencmd", "get_throttled"], timeout=3)
        if rc == 0:
            h["throttled"] = decode_throttled(out)
        rc, out = _run(["vcgencmd", "measure_temp"], timeout=3)
        match = re.search(r"([\d.]+)", out) if rc == 0 else None
        if match:
            h["gpu_temp"] = float(match.group(1))

    h["reboot_required"] = os.path.exists("/var/run/reboot-required")
    # The database is three files while the app runs: the main file plus its write-ahead log.
    h["db_size"] = sum(os.path.getsize(DB_PATH + suffix) for suffix in ("", "-wal", "-shm")
                       if os.path.exists(DB_PATH + suffix))
    h["photos_size"], h["photos_count"] = dir_size(os.path.join(DATA_DIR, "photos"))
    backups = list_backups()
    h["backups_count"] = len(backups)
    h["backups_size"] = sum(b["size"] for b in backups)
    h["last_backup"] = backups[0]["mtime"] if backups else None

    warnings = []
    for disk in disks:
        if disk["free_percent"] < 10:
            warnings.append("%s storage is almost full: %s left." % (disk["label"], human_bytes(disk["free"])))
    if h["cpu_temp"] is not None and h["cpu_temp"] >= 80:
        warnings.append("CPU is hot at %.0f C. Check cooling." % h["cpu_temp"])
    if h["mem"]["percent"] >= 90:
        warnings.append("Memory is nearly full.")
    for message in h["throttled"] or []:
        warnings.append(message + ".")
    if h["reboot_required"]:
        warnings.append("An installed update needs a reboot to take effect.")
    if h["on_sd_card"]:
        warnings.append("The system runs from an SD card. Keep backups current until it moves to an SSD.")
    if h["last_backup"] is None or time.time() - h["last_backup"] > 2 * 86400:
        warnings.append("No local database backup in the last two days.")
    try:
        import remote
        warnings.extend(remote.warnings())
    except Exception:
        pass
    if shutil.which("systemctl") and os.path.isdir("/run/systemd/system") and not autostart_status()["ready"]:
        warnings.append("Bench Log will not start by itself after a reboot. Turn on start at boot below.")
    h["warnings"] = warnings
    return h


# ----------------------------------------------------------- admin password

def _secrets():
    return load_json("secrets.json")


def admin_is_set():
    return bool(_secrets().get("admin"))


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000).hex()


def set_admin(password):
    data = _secrets()
    salt = os.urandom(16)
    data["admin"] = {"salt": salt.hex(), "hash": _hash(password, salt)}
    save_json("secrets.json", data, private=True)


def check_admin(password):
    record = _secrets().get("admin")
    if not record or not password:
        return False
    return hmac.compare_digest(_hash(password, bytes.fromhex(record["salt"])), record["hash"])


def get_secret(section):
    return _secrets().get(section) or {}


def set_secret(section, value):
    data = _secrets()
    data[section] = value
    save_json("secrets.json", data, private=True)


def sudo_passwordless():
    """True when this user can run sudo without typing a password."""
    if os.geteuid() == 0:
        return True
    if not shutil.which("sudo"):
        return False
    return _run(["sudo", "-n", "true"], timeout=5)[0] == 0


def sudo_argv(argv, password):
    """Build a sudo command. The password, if any, is fed on stdin and never placed in argv."""
    if os.geteuid() == 0:
        return list(argv), None  # already root, so sudo is not involved
    if password:
        return ["sudo", "-S", "-k", "-p", ""] + argv, password + "\n"
    return ["sudo", "-n"] + argv, None


# --------------------------------------------------------------------- jobs

_jobs = {}
_jobs_lock = threading.Lock()


class Step:
    """One step of a job: either a command or a Python function."""

    def __init__(self, title, argv=None, func=None, sudo_password=None, use_sudo=False, timeout=3600,
                 cwd=None, env=None):
        self.title, self.argv, self.func = title, argv, func
        self.sudo_password, self.use_sudo, self.timeout, self.cwd = sudo_password, use_sudo, timeout, cwd
        self.env = env


def _job_paths(job_id):
    base = os.path.join(DATA_DIR, "logs", job_id)
    return base + ".json", base + ".log"


def _save_job(job):
    meta_path, _ = _job_paths(job["id"])
    public = {k: v for k, v in job.items() if k != "steps"}
    with open(meta_path, "w") as fh:
        json.dump(public, fh)


def get_job(job_id):
    if not re.match(r"^[a-z-]+-\d{8}-\d{6}$", job_id or ""):
        return None
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        meta_path, _ = _job_paths(job_id)
        try:
            with open(meta_path) as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            return None
        # A job found only on disk was running when the app stopped, so it cannot still be running.
        if job.get("status") == "running":
            job["status"] = "restarted" if job.get("restarts") else "interrupted"
    return job


def job_log(job_id, tail=400):
    _, log_path = _job_paths(job_id)
    try:
        with open(log_path, errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    return "".join(lines[-tail:])


def running_job():
    with _jobs_lock:
        for job in _jobs.values():
            if job["status"] == "running":
                return job
    return None


def start_job(kind, title, steps, on_failure=None, on_success=None, restarts=False):
    """Run steps one after another in a background thread. Only one job runs at a time."""
    if running_job() is not None:
        return None
    job_id = "%s-%s" % (kind, time.strftime("%Y%m%d-%H%M%S"))
    job = {"id": job_id, "kind": kind, "title": title, "status": "running", "started": int(time.time()),
           "ended": None, "restarts": restarts, "message": ""}
    with _jobs_lock:
        _jobs[job_id] = job
    _save_job(job)

    def log(text):
        _, log_path = _job_paths(job_id)
        with open(log_path, "a") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")

    def work():
        ok = True
        for step in steps:
            log("\n== %s ==" % step.title)
            try:
                if step.func is not None:
                    result = step.func(log)
                    if result is False:
                        ok = False
                else:
                    ok = _run_step(step, log)
            except Exception as exc:  # a failed step must never kill the app
                log("Error: %s" % exc)
                ok = False
            if not ok:
                log("Stopped: this step failed.")
                break
        if not ok and on_failure is not None:
            try:
                on_failure(log)
            except Exception as exc:
                log("Cleanup error: %s" % exc)
        job["status"] = "done" if ok else "failed"
        job["ended"] = int(time.time())
        _save_job(job)
        if ok and on_success is not None:
            on_success(log)

    threading.Thread(target=work, daemon=True).start()
    return job


def _run_step(step, log):
    argv, stdin_text = list(step.argv), None
    if step.use_sudo:
        argv, stdin_text = sudo_argv(argv, step.sudo_password)
    shown = step.argv if step.use_sudo else argv
    log("$ %s%s" % ("sudo " if step.use_sudo else "", " ".join(shown)))
    env = dict(step.env or os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, cwd=step.cwd, env=env)
    except OSError as exc:
        log("Could not start: %s" % exc)
        return False
    try:
        if stdin_text:
            proc.stdin.write(stdin_text)
        proc.stdin.close()
    except OSError:
        pass
    deadline = time.time() + step.timeout
    for line in proc.stdout:
        log(line.rstrip("\n"))
        if time.time() > deadline:
            proc.kill()
            log("Timed out.")
            return False
    code = proc.wait()
    if code != 0:
        log("Exit code %d" % code)
    return code == 0


# ------------------------------------------------------------------ backups

def snapshot_to(path):
    """Write a consistent, verified copy of the live database to any path."""
    if os.path.exists(path):
        os.remove(path)
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(path)
    try:
        with target:
            source.backup(target)
        check = target.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if check != "ok":
        os.remove(path)
        raise RuntimeError("Backup failed its integrity check: %s" % check)


def create_backup(kind="manual"):
    """Write a consistent copy of the live database and return its file name."""
    name = "%s-%s.db" % (kind, time.strftime("%Y%m%d-%H%M%S"))
    snapshot_to(os.path.join(DATA_DIR, "backups", name))
    prune_backups()
    return name


def list_backups():
    folder = os.path.join(DATA_DIR, "backups")
    rows = []
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if not BACKUP_NAME.match(name):
            continue
        path = os.path.join(folder, name)
        rows.append({"name": name, "kind": name.rsplit("-", 2)[0], "size": os.path.getsize(path),
                     "mtime": int(os.path.getmtime(path))})
    return sorted(rows, key=lambda r: r["mtime"], reverse=True)


def prune_backups():
    by_kind = {}
    for row in list_backups():
        by_kind.setdefault(row["kind"], []).append(row)
    for kind, rows in by_kind.items():
        keep = KEEP_AUTO if kind == "auto" else KEEP_OTHER
        for row in rows[keep:]:
            try:
                os.remove(os.path.join(DATA_DIR, "backups", row["name"]))
            except OSError:
                pass


def backup_path(name):
    if not BACKUP_NAME.match(name or ""):
        return None
    path = os.path.join(DATA_DIR, "backups", name)
    return path if os.path.exists(path) else None


def restore_backup(name):
    """Replace the live database with a backup, after saving the current one first."""
    path = backup_path(name)
    if path is None:
        raise RuntimeError("That backup no longer exists.")
    source = sqlite3.connect(path)
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("That backup is damaged, so nothing was changed.")
        saved = create_backup("pre-restore")
        target = sqlite3.connect(DB_PATH)
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return saved


def _tables(conn, schema):
    return [r[0] for r in conn.execute(
        "SELECT name FROM %s.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%%' ORDER BY name"
        % schema)]


def _columns(conn, schema, table):
    info = conn.execute("PRAGMA %s.table_info(%s)" % (schema, table)).fetchall()
    keys = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    return [row[1] for row in info], keys


TABLE_LABELS = {
    "devices": "Devices", "repair_items": "Repair steps", "work_sessions": "Time sessions",
    "device_tests": "Checklist results", "device_parts": "Donor part states", "photos": "Photo records",
    "boxes": "Boxes", "lots": "Lots", "parts": "Catalog parts", "functions": "Checklist items",
    "device_types": "Device types", "part_functions": "Part and symptom links", "settings": "Settings",
}


def compare_backup(name):
    """Describe how a backup differs from the live database, table by table.

    For each table: rows only in the live data (a full restore would lose them),
    rows only in the backup (a restore or merge would bring them back), and rows
    present in both but different.
    """
    path = backup_path(name)
    if path is None:
        raise RuntimeError("That backup no longer exists.")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ATTACH DATABASE ? AS bk", (path,))
        live_tables, backup_tables = _tables(conn, "main"), set(_tables(conn, "bk"))
        result = []
        for table in live_tables:
            entry = {"table": table, "label": TABLE_LABELS.get(table, table), "only_live": 0, "only_backup": 0,
                     "changed": 0, "same": 0, "live_samples": [], "backup_samples": []}
            if table not in backup_tables:
                entry["only_live"] = conn.execute("SELECT COUNT(*) FROM main.%s" % table).fetchone()[0]
                entry["note"] = "This table did not exist when the backup was made."
                result.append(entry)
                continue
            live_cols, keys = _columns(conn, "main", table)
            backup_cols, _ = _columns(conn, "bk", table)
            shared = [c for c in live_cols if c in backup_cols]
            keys = [k for k in keys if k in shared] or shared
            key_index = [shared.index(k) for k in keys]
            select = "SELECT %s FROM %%s.%s" % (", ".join(shared), table)
            live = {tuple(row[i] for i in key_index): row for row in conn.execute(select % "main")}
            backup = {tuple(row[i] for i in key_index): row for row in conn.execute(select % "bk")}
            code_index = shared.index("code") if "code" in shared else None

            def sample(row, key):
                return str(row[code_index]) if code_index is not None else "/".join(str(k) for k in key)

            for key, row in live.items():
                if key not in backup:
                    entry["only_live"] += 1
                    if len(entry["live_samples"]) < 40:
                        entry["live_samples"].append(sample(row, key))
                elif backup[key] != row:
                    entry["changed"] += 1
                else:
                    entry["same"] += 1
            for key, row in backup.items():
                if key not in live:
                    entry["only_backup"] += 1
                    if len(entry["backup_samples"]) < 40:
                        entry["backup_samples"].append(sample(row, key))
            result.append(entry)
        return result
    finally:
        conn.close()


def merge_backup(name):
    """Bring back records that exist in a backup but are missing now. Nothing current is changed.

    Returns (name of the safety backup made first, number of records added).
    """
    path = backup_path(name)
    if path is None:
        raise RuntimeError("That backup no longer exists.")
    saved = create_backup("pre-restore")
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("ATTACH DATABASE ? AS bk", (path,))
        backup_tables = set(_tables(conn, "bk"))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        before = conn.total_changes
        for table in _tables(conn, "main"):
            if table not in backup_tables or table == "settings":
                continue
            live_cols, _ = _columns(conn, "main", table)
            backup_cols, _ = _columns(conn, "bk", table)
            shared = ", ".join(c for c in live_cols if c in backup_cols)
            conn.execute("INSERT OR IGNORE INTO main.%s (%s) SELECT %s FROM bk.%s" % (table, shared, shared, table))
        added = conn.total_changes - before
        broken = conn.execute("PRAGMA main.foreign_key_check").fetchall()
        if broken:
            conn.execute("ROLLBACK")
            raise RuntimeError(
                "Some missing records depend on others that conflict with current data, so nothing was "
                "changed. Use a full restore instead, or copy what you need by hand.")
        conn.execute("COMMIT")
        return saved, added
    finally:
        conn.close()


def full_archive():
    """Zip the database and every photo into one file and return its path."""
    path = os.path.join(DATA_DIR, "full-backup.zip")
    name = create_backup("manual")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        archive.write(os.path.join(DATA_DIR, "backups", name), "benchlog.db")
        photos = os.path.join(DATA_DIR, "photos")
        for root, _dirs, files in os.walk(photos):
            for filename in files:
                full = os.path.join(root, filename)
                archive.write(full, os.path.join("photos", os.path.relpath(full, photos)))
    return path


def backup_loop():
    """Keep one automatic backup per day. Runs forever in a background thread."""
    while True:
        try:
            today = time.strftime("%Y%m%d")
            if not any(b["kind"] == "auto" and b["name"].startswith("auto-" + today) for b in list_backups()):
                create_backup("auto")
        except Exception as exc:
            print("Automatic backup failed: %s" % exc, file=sys.stderr)
        try:
            import remote
            remote.tick()
        except Exception as exc:
            print("Remote backup check failed: %s" % exc, file=sys.stderr)
        time.sleep(3600)


# ---------------------------------------------------------------- app (git)

def git(*args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return _run(["git", "-C", APP_DIR] + list(args), timeout=timeout, env=env)


def app_version():
    """Describe the installed code: commit, branch, date. Empty values when not a git checkout."""
    info = {"is_git": False, "commit": "", "branch": "", "date": "", "remote": "", "dirty": False}
    if not shutil.which("git") or git("rev-parse", "--is-inside-work-tree")[0] != 0:
        return info
    # Refuse to treat a parent folder's repository as ours.
    rc, top = git("rev-parse", "--show-toplevel")
    if rc != 0 or os.path.realpath(top) != os.path.realpath(APP_DIR):
        return info
    info["is_git"] = True
    info["commit"] = git("rev-parse", "--short", "HEAD")[1]
    info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")[1]
    info["date"] = git("log", "-1", "--format=%cd", "--date=short")[1]
    rc, remote = git("remote", "get-url", "origin")
    # Never show credentials that may be embedded in a remote address.
    info["remote"] = re.sub(r"//[^/@]+@", "//", remote) if rc == 0 else ""
    info["dirty"] = bool(git("status", "--porcelain", "--untracked-files=no")[1])
    return info


def check_app_update():
    """Fetch from the remote and report what is new. Saves the result for the System page."""
    version = app_version()
    result = {"checked_at": int(time.time()), "error": "", "behind": 0, "commits": [], "files": "",
              "branch": version["branch"]}
    if not version["is_git"]:
        result["error"] = "This copy was not installed with git, so it cannot update itself."
    elif not version["remote"]:
        result["error"] = "No git remote named origin is set."
    else:
        rc, out = git("fetch", "--prune", "origin", timeout=90)
        upstream = "origin/" + version["branch"]
        if rc != 0:
            result["error"] = "Could not reach the repository: " + (out.splitlines()[-1] if out else "unknown error")
        elif git("rev-parse", "--verify", "--quiet", upstream)[0] != 0:
            result["error"] = "The remote has no branch called %s." % version["branch"]
        else:
            rc, count = git("rev-list", "--count", "HEAD.." + upstream)
            result["behind"] = int(count) if rc == 0 and count.isdigit() else 0
            rc, log_text = git("log", "--format=%h%x09%ad%x09%s", "--date=short", "HEAD.." + upstream)
            for line in log_text.splitlines() if rc == 0 else []:
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    result["commits"].append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
            result["files"] = git("diff", "--stat", "HEAD.." + upstream)[1] if result["behind"] else ""
    save_json("app-update.json", result)
    return result


def update_steps(restart):
    """Steps that update the code safely: back up, fast-forward, install requirements, restart."""
    version = app_version()
    state = {}

    def backup(log):
        name = create_backup("pre-update")
        log("Database saved as %s" % name)

    def remember(log):
        state["previous"] = git("rev-parse", "HEAD")[1]
        log("Current version: %s" % state["previous"][:10])

    def record(log):
        new = git("rev-parse", "HEAD")[1]
        save_json("update-state.json", {"previous": state.get("previous", ""), "current": new,
                                        "at": int(time.time())})
        log("Now on %s" % new[:10])
        log("Restarting the app. This page reconnects by itself.")

    def undo(log):
        if state.get("previous"):
            git("reset", "--hard", state["previous"])
            log("Put the code back to %s. Nothing else was changed." % state["previous"][:10])

    steps = [
        Step("Back up the database", func=backup),
        Step("Note the current version", func=remember),
        Step("Download and apply the update", argv=["git", "-C", APP_DIR, "merge", "--ff-only",
                                                    "origin/" + version["branch"]], timeout=120),
        Step("Install requirements", argv=[sys.executable, "-m", "pip", "install", "-q", "-r",
                                           os.path.join(APP_DIR, "requirements.txt")], timeout=900),
        Step("Record the new version", func=record),
    ]
    return steps, undo, restart


def rollback_steps(restart):
    state = load_json("update-state.json")
    previous = state.get("previous", "")

    def backup(log):
        log("Database saved as %s" % create_backup("pre-update"))

    def record(log):
        save_json("update-state.json", {"previous": "", "current": previous, "at": int(time.time())})
        log("Back on %s. Restarting the app." % previous[:10])

    steps = [
        Step("Back up the database", func=backup),
        Step("Return to the previous version", argv=["git", "-C", APP_DIR, "reset", "--hard", previous]),
        Step("Install requirements", argv=[sys.executable, "-m", "pip", "install", "-q", "-r",
                                           os.path.join(APP_DIR, "requirements.txt")], timeout=900),
        Step("Record the version", func=record),
    ]
    return steps, restart


def restart_app(delay=1.5):
    """Restart the app process shortly after the current request has been answered."""
    def go():
        time.sleep(delay)
        if os.environ.get("INVOCATION_ID") and shutil.which("systemctl"):
            subprocess.Popen(["systemctl", "--user", "restart", UNIT_NAME], env=_user_env())
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=go, daemon=True).start()


# ------------------------------------------------------------ start at boot

UNIT_NAME = os.environ.get("BENCHLOG_UNIT", "benchlog")


def _user_env():
    """systemctl --user needs to know where the user's session bus lives."""
    env = dict(os.environ)
    runtime = "/run/user/%d" % os.getuid()
    if "XDG_RUNTIME_DIR" not in env and os.path.isdir(runtime):
        env["XDG_RUNTIME_DIR"] = runtime
    return env


def unit_path():
    return os.path.join(os.path.expanduser("~"), ".config", "systemd", "user", UNIT_NAME + ".service")


def unit_text():
    """The service definition, written for wherever this copy is actually installed."""
    return "\n".join([
        "[Unit]",
        "Description=Bench Log repair tracker",
        "After=network-online.target",
        "",
        "[Service]",
        "WorkingDirectory=%s" % APP_DIR,
        "ExecStart=%s %s" % (sys.executable, os.path.join(APP_DIR, "app.py")),
        "Environment=BENCHLOG_PORT=%s" % os.environ.get("BENCHLOG_PORT", "8080"),
        "Environment=BENCHLOG_DATA=%s" % DATA_DIR,
        "Restart=on-failure",
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def user_name():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER", "")


def autostart_status():
    """Whether Bench Log comes back by itself after a reboot, and what is missing if not."""
    status = {"available": bool(shutil.which("systemctl")), "installed": os.path.exists(unit_path()),
              "enabled": False, "linger": False, "as_service": bool(os.environ.get("INVOCATION_ID"))}
    if status["available"]:
        status["enabled"] = _run(["systemctl", "--user", "is-enabled", UNIT_NAME], timeout=5,
                                 env=_user_env())[0] == 0
        rc, out = _run(["loginctl", "show-user", user_name(), "-p", "Linger"], timeout=5)
        status["linger"] = rc == 0 and "Linger=yes" in out
    status["ready"] = status["installed"] and status["enabled"] and status["linger"]
    return status


def autostart_steps(sudo_password):
    """Install and enable the user service, and let it run while nobody is logged in."""
    def write_unit(log):
        os.makedirs(os.path.dirname(unit_path()), exist_ok=True)
        with open(unit_path(), "w") as fh:
            fh.write(unit_text())
        log("Wrote %s" % unit_path())

    return [
        Step("Write the service file", func=write_unit),
        Step("Reload the service list", argv=["systemctl", "--user", "daemon-reload"], env=_user_env()),
        Step("Start at boot", argv=["systemctl", "--user", "enable", UNIT_NAME], env=_user_env()),
        Step("Keep running when logged out", argv=["loginctl", "enable-linger", user_name()], use_sudo=True,
             sudo_password=sudo_password),
    ]


def hand_over_to_service(delay=1.5):
    """Stop this hand-started copy and let the service take over the same port."""
    def go():
        time.sleep(delay)
        subprocess.Popen(["sh", "-c", "sleep 2; systemctl --user start %s" % shlex.quote(UNIT_NAME)],
                         start_new_session=True, env=_user_env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        os._exit(0)

    threading.Thread(target=go, daemon=True).start()


# ----------------------------------------------------------------- OS (apt)

UPGRADABLE = re.compile(r"^(?P<name>[^/\s]+)/\S+\s+(?P<new>\S+)\s+\S+\s+\[upgradable from: (?P<old>[^\]]+)\]")


def parse_upgradable(text):
    packages = []
    for line in (text or "").splitlines():
        match = UPGRADABLE.match(line.strip())
        if match:
            packages.append(match.groupdict())
    return packages


def has_apt():
    return bool(shutil.which("apt-get"))


def os_check_steps(sudo_password):
    def collect(log):
        rc, out = _run(["apt", "list", "--upgradable"], timeout=120, env=dict(os.environ, LC_ALL="C"))
        packages = parse_upgradable(out)
        save_json("os-updates.json", {"checked_at": int(time.time()), "packages": packages})
        log("%d package(s) can be updated." % len(packages))
        for p in packages:
            log("  %s  %s -> %s" % (p["name"], p["old"], p["new"]))

    return [
        Step("Refresh the package lists", argv=["apt-get", "update"], use_sudo=True,
             sudo_password=sudo_password, timeout=600),
        Step("List available updates", func=collect),
    ]


def os_upgrade_steps(sudo_password):
    def before(log):
        pending = load_json("os-updates.json").get("packages", [])
        log("Versions before this update, in case one needs to be put back by hand:")
        for p in pending:
            log("  %s=%s" % (p["name"], p["old"]))

    def after(log):
        save_json("os-updates.json", {"checked_at": int(time.time()), "packages": []})
        if os.path.exists("/var/run/reboot-required"):
            log("A reboot is needed to finish. Use the Reboot button when the bench is idle.")

    return [
        Step("Record current versions", func=before),
        Step("Install updates", use_sudo=True, sudo_password=sudo_password, timeout=3600,
             argv=["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y",
                   "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold",
                   "upgrade"]),
        Step("Finish", func=after),
    ]


def reboot(sudo_password, delay=2.0):
    argv, stdin_text = sudo_argv(["systemctl", "reboot"], sudo_password)

    def go():
        time.sleep(delay)
        try:
            subprocess.run(argv, input=stdin_text, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass

    threading.Thread(target=go, daemon=True).start()


def verify_sudo(password):
    """Check a sudo password without running anything."""
    if not shutil.which("sudo"):
        return False
    argv, stdin_text = sudo_argv(["true"], password)
    try:
        return subprocess.run(argv, input=stdin_text, text=True, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
