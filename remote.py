"""Remote backup over SSH: timestamped database snapshots and a mirror of the photos.

The Pi signs in to the backup server with its own key, so no password is
stored. Nothing on the server is ever deleted except database snapshots
beyond the configured number to keep, and photos are never deleted.
"""

import hashlib
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import time

import system

SNAPSHOT_NAME = re.compile(r"^benchlog-(\d{8})-(\d{6})\.db$")
HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
DIRECTORY = re.compile(r"^[A-Za-z0-9_./~-]{1,200}$")

DEFAULTS = {"enabled": False, "host": "", "user": "", "port": 22, "directory": "~/benchlog-backups",
            "interval_days": 3, "keep": 30}


def key_path():
    return os.path.join(system.DATA_DIR, "remote_backup_key")


def known_hosts_path():
    return os.path.join(system.DATA_DIR, "remote_known_hosts")


def config():
    saved = system.load_json("remote.json")
    return {key: saved.get(key, default) for key, default in DEFAULTS.items()}


def state():
    return system.load_json("remote-state.json")


def _save_state(**changes):
    current = state()
    current.update(changes)
    system.save_json("remote-state.json", current)
    return current


def validate(cfg):
    """Return a list of problems with a configuration. Empty means it is usable."""
    problems = []
    if not HOST.match(str(cfg.get("host", ""))):
        problems.append("The server address should be a host name or IP address, like backup.example.com.")
    if not USER.match(str(cfg.get("user", ""))):
        problems.append("The user name contains characters that are not allowed.")
    directory = str(cfg.get("directory", ""))
    if not DIRECTORY.match(directory) or directory.startswith("-") or ".." in directory.split("/"):
        problems.append("The folder may only contain letters, numbers, and . _ - / ~ characters.")
    try:
        if not 1 <= int(cfg.get("port", 0)) <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("The port should be a number between 1 and 65535.")
    try:
        if not 1 <= int(cfg.get("interval_days", 0)) <= 90:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("Back up every 1 to 90 days.")
    try:
        if not 2 <= int(cfg.get("keep", 0)) <= 1000:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("Keep between 2 and 1000 snapshots.")
    return problems


def save_config(cfg):
    problems = validate(cfg)
    if problems:
        return problems
    clean = {"enabled": bool(cfg.get("enabled")), "host": cfg["host"], "user": cfg["user"],
             "port": int(cfg["port"]), "directory": cfg["directory"].rstrip("/") or "/",
             "interval_days": int(cfg["interval_days"]), "keep": int(cfg["keep"])}
    system.save_json("remote.json", clean)
    return []


def is_configured(cfg=None):
    cfg = cfg or config()
    return bool(cfg["host"] and cfg["user"]) and not validate(cfg)


# ---------------------------------------------------------------------- key

def has_key():
    return os.path.exists(key_path()) and os.path.exists(key_path() + ".pub")


def ensure_key():
    """Create the Pi's backup key pair if it does not exist yet."""
    if has_key():
        return
    if not shutil.which("ssh-keygen"):
        raise RuntimeError("ssh-keygen is not installed. Run: sudo apt install openssh-client")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "benchlog-backup@" + socket.gethostname(),
                    "-f", key_path()], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.chmod(key_path(), 0o600)


def public_key():
    try:
        with open(key_path() + ".pub") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def server_setup_commands(cfg=None):
    """What to run once on the backup server so it accepts this Pi's key."""
    cfg = cfg or config()
    directory = cfg["directory"]
    return "\n".join([
        "mkdir -p %s && chmod 700 %s" % (directory, directory),
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh",
        "echo '%s' >> ~/.ssh/authorized_keys" % public_key(),
        "chmod 600 ~/.ssh/authorized_keys",
    ])


# --------------------------------------------------------------- transport

def _ssh_options(cfg):
    return ["-i", key_path(), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=" + known_hosts_path()]


def _ssh(cfg):
    return ["ssh", "-p", str(cfg["port"])] + _ssh_options(cfg) + ["%s@%s" % (cfg["user"], cfg["host"])]


def _remote_dir(cfg):
    """The backup folder as a safely quoted shell expression for the server."""
    directory = cfg["directory"]
    if directory == "~":
        return '"$HOME"'
    if directory.startswith("~/"):
        return '"$HOME"/' + shlex.quote(directory[2:])
    return shlex.quote(directory)


def run_remote(cfg, command, timeout=60, stdin_path=None, stdout_path=None):
    """Run one command on the server. Returns (exit code, text output)."""
    if not shutil.which("ssh"):
        return 127, "ssh is not installed. Run: sudo apt install openssh-client"
    if not has_key():
        return 126, "No backup key yet. Create one first."
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    try:
        proc = subprocess.run(_ssh(cfg) + [command], stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                              timeout=timeout)
        out = b"" if stdout_path else proc.stdout
        text = (out + proc.stderr).decode("utf-8", "replace").strip()
        return proc.returncode, text
    except subprocess.TimeoutExpired:
        return 124, "The server did not answer in time."
    except OSError as exc:
        return 127, str(exc)
    finally:
        if stdin_path:
            stdin.close()
        if stdout_path:
            stdout.close()


def friendly_error(text):
    """Turn common SSH failures into something a person can act on."""
    lowered = (text or "").lower()
    if "permission denied" in lowered:
        return "The server refused the key. Run the setup commands on the server, then test again."
    if "host key verification failed" in lowered or "remote host identification has changed" in lowered:
        return ("The server's identity changed since the first connection. If you reinstalled the server, "
                "use \"Forget server identity\" and test again. Otherwise do not continue.")
    if "connection refused" in lowered:
        return "The server refused the connection. Check that SSH is running there and the port is right."
    if "timed out" in lowered or "did not answer" in lowered or "no route to host" in lowered:
        return "The server could not be reached. It may be off, or a firewall is blocking the port."
    if "could not resolve" in lowered or "name or service not known" in lowered:
        return "That server name could not be found. Check the address."
    last = (text or "").strip().splitlines()
    return last[-1] if last else "Unknown error."


# ------------------------------------------------------------------- health

def parse_listing(text):
    """Pick database snapshots out of 'ls -l' output. Returns newest first."""
    rows = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        match = SNAPSHOT_NAME.match(parts[-1])
        if not match or not parts[4].isdigit():
            continue
        try:
            when = int(time.mktime(time.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")))
        except ValueError:
            continue
        rows.append({"name": parts[-1], "size": int(parts[4]), "when": when})
    return sorted(rows, key=lambda r: r["name"], reverse=True)


def check(cfg=None):
    """Connect, make sure the folder exists, and read free space and the snapshot list."""
    cfg = cfg or config()
    result = {"checked_at": int(time.time()), "reachable": False, "error": "", "snapshots": [],
              "free": None, "path": "", "rsync": False, "latency_ms": None}
    if not is_configured(cfg):
        result["error"] = "Enter the server address and user first."
    else:
        started = time.time()
        directory = _remote_dir(cfg)
        command = ("mkdir -p %s && cd %s && echo BENCHLOG_OK && pwd -P && "
                   "(command -v rsync >/dev/null 2>&1 && echo HAS_RSYNC || echo NO_RSYNC) && "
                   "df -Pk . | tail -1 && echo LISTING && ls -l") % (directory, directory)
        code, out = run_remote(cfg, command, timeout=30)
        if code != 0 or "BENCHLOG_OK" not in out:
            result["error"] = friendly_error(out)
        else:
            result["reachable"] = True
            result["latency_ms"] = int((time.time() - started) * 1000)
            lines = out[out.index("BENCHLOG_OK"):].splitlines()
            result["path"] = lines[1].strip() if len(lines) > 1 else ""
            result["rsync"] = "HAS_RSYNC" in out
            for line in lines:
                fields = line.split()
                # The df line: filesystem, total, used, available (in KB), percent, mount point.
                if len(fields) >= 6 and fields[1].isdigit() and fields[3].isdigit() and fields[4].endswith("%"):
                    result["free"] = int(fields[3]) * 1024
                    break
            listing = out.split("LISTING", 1)[1] if "LISTING" in out else ""
            result["snapshots"] = parse_listing(listing)
    _save_state(check=result)
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------- backup

def push(log, cfg=None):
    """Send a fresh database snapshot and any new photos to the server."""
    cfg = cfg or config()
    health = check(cfg)
    if not health["reachable"]:
        _save_state(last_error=health["error"], last_attempt_at=int(time.time()))
        raise RuntimeError(health["error"])
    directory = _remote_dir(cfg)
    name = "benchlog-%s.db" % time.strftime("%Y%m%d-%H%M%S")
    local = os.path.join(system.DATA_DIR, "remote-upload.db")
    try:
        system.snapshot_to(local)
        expected = file_hash(local)
        log("Snapshot %s is %s." % (name, system.human_bytes(os.path.getsize(local))))

        # Upload under a temporary name, verify, then rename, so a broken upload never looks complete.
        quoted = shlex.quote(name)
        code, out = run_remote(cfg, "cat > %s/%s.part" % (directory, quoted), timeout=600, stdin_path=local)
        if code != 0:
            raise RuntimeError("Upload failed: " + friendly_error(out))
        code, out = run_remote(
            cfg, "cd %s && (sha256sum %s.part 2>/dev/null || shasum -a 256 %s.part) | cut -d' ' -f1"
            % (directory, quoted, quoted), timeout=120)
        if code != 0 or out.strip().splitlines()[-1].strip() != expected:
            run_remote(cfg, "rm -f %s/%s.part" % (directory, quoted))
            raise RuntimeError("The copy on the server does not match the original, so it was discarded.")
        code, out = run_remote(cfg, "mv %s/%s.part %s/%s" % (directory, quoted, directory, quoted))
        if code != 0:
            raise RuntimeError("Could not finish the upload: " + friendly_error(out))
        log("Uploaded and verified.")
    finally:
        if os.path.exists(local):
            os.remove(local)

    photos_note = sync_photos(log, cfg, health, direction="up")

    # Remove the oldest snapshots beyond the number to keep. Only names this app created are touched.
    snapshots = check(cfg)["snapshots"]
    for old in snapshots[int(cfg["keep"]):]:
        if SNAPSHOT_NAME.match(old["name"]):
            run_remote(cfg, "rm -f %s/%s" % (directory, shlex.quote(old["name"])))
            log("Removed old snapshot %s." % old["name"])
    check(cfg)
    _save_state(last_backup_at=int(time.time()), last_backup_name=name, last_error="",
                last_attempt_at=int(time.time()), photos_note=photos_note)
    return name


def sync_photos(log, cfg, health, direction="up"):
    """Copy photos to the server (up) or back from it (down). Never deletes on either side."""
    local = os.path.join(system.DATA_DIR, "photos")
    os.makedirs(local, exist_ok=True)
    directory = _remote_dir(cfg)
    if direction == "up" and not any(files for _root, _dirs, files in os.walk(local)):
        log("No photos to copy.")
        return "No photos yet."
    if shutil.which("rsync") and health.get("rsync") and health.get("path"):
        shell = "ssh -p %d %s" % (int(cfg["port"]), " ".join(shlex.quote(o) for o in _ssh_options(cfg)))
        remote_path = "%s@%s:%s/photos/" % (cfg["user"], cfg["host"], health["path"])
        pair = [local + "/", remote_path] if direction == "up" else [remote_path, local + "/"]
        if direction == "up":
            run_remote(cfg, "mkdir -p %s/photos" % directory)
        proc = subprocess.run(["rsync", "-a", "-e", shell] + pair, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=3600)
        if proc.returncode != 0:
            # 23 and 24 mean some files could not be copied, for example when none exist yet.
            if direction == "down" and proc.returncode in (23, 24):
                log("No photos on the server yet.")
                return "No photos on the server."
            raise RuntimeError("Photo copy failed: " + friendly_error(proc.stdout))
        log("Photos are in sync (only new files were sent).")
        return "Synced with rsync."
    # Without rsync on both ends, stream everything through tar. Slower, but needs nothing extra.
    if direction == "up":
        packer = subprocess.Popen(["tar", "-C", system.DATA_DIR, "-cf", "-", "photos"], stdout=subprocess.PIPE)
        receiver = subprocess.run(_ssh(cfg) + ["tar -C %s -xf -" % directory], stdin=packer.stdout,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
        packer.wait()
        if receiver.returncode != 0:
            raise RuntimeError("Photo copy failed: " + friendly_error(receiver.stdout.decode("utf-8", "replace")))
    else:
        sender = subprocess.Popen(_ssh(cfg) + ["cd %s && test -d photos && tar -cf - photos" % directory],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        unpack = subprocess.run(["tar", "-C", system.DATA_DIR, "-xkf", "-"], stdin=sender.stdout,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
        sender.wait()
        if sender.returncode != 0:
            log("No photos on the server yet.")
            return "No photos on the server."
    log("Photos copied (full copy, because rsync is not on both machines).")
    return "Copied with tar."


def fetch(name, cfg=None):
    """Download one snapshot into the local backups folder and return its local name."""
    cfg = cfg or config()
    match = SNAPSHOT_NAME.match(name or "")
    if not match:
        raise RuntimeError("That is not a snapshot name.")
    local_name = "fetched-%s-%s.db" % (match.group(1), match.group(2))
    target = os.path.join(system.DATA_DIR, "backups", local_name)
    partial = target + ".part"
    code, out = run_remote(cfg, "cat %s/%s" % (_remote_dir(cfg), shlex.quote(name)), timeout=600,
                           stdout_path=partial)
    if code != 0:
        if os.path.exists(partial):
            os.remove(partial)
        raise RuntimeError("Download failed: " + friendly_error(out))
    try:
        check_db = sqlite3.connect(partial)
        ok = check_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        check_db.close()
    except sqlite3.DatabaseError:
        ok = False
    if not ok:
        os.remove(partial)
        raise RuntimeError("The downloaded snapshot is damaged, so it was discarded.")
    os.replace(partial, target)
    system.prune_backups()
    return local_name


def forget_host():
    path = known_hosts_path()
    if os.path.exists(path):
        os.remove(path)


# ----------------------------------------------------------------- schedule

def is_due(cfg=None, now=None):
    cfg = cfg or config()
    if not cfg["enabled"] or not is_configured(cfg) or not has_key():
        return False
    last = state().get("last_backup_at") or 0
    return (now or time.time()) - last >= int(cfg["interval_days"]) * 86400


def tick():
    """Called about once an hour: check the server, and back up when one is due."""
    cfg = config()
    if not cfg["enabled"] or not is_configured(cfg) or not has_key():
        return
    health = check(cfg)
    if not health["reachable"] or not is_due(cfg):
        return
    log_path = os.path.join(system.DATA_DIR, "logs", "remote-auto.log")

    def log(text):
        with open(log_path, "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))

    try:
        log("Scheduled backup started.")
        log("Finished: %s" % push(log, cfg))
    except Exception as exc:
        log("Failed: %s" % exc)
        _save_state(last_error=str(exc), last_attempt_at=int(time.time()))


def warnings():
    """Problems worth showing on the System page."""
    cfg, current = config(), state()
    if not cfg["enabled"]:
        return []
    found = []
    health = current.get("check") or {}
    if health and not health.get("reachable"):
        found.append("Remote backup server problem: %s" % (health.get("error") or "not reachable"))
    last = current.get("last_backup_at")
    limit = (int(cfg["interval_days"]) + 2) * 86400
    if not last:
        found.append("Remote backup is on, but no backup has reached the server yet.")
    elif time.time() - last > limit:
        found.append("The last remote backup was %s ago." % system.human_duration(time.time() - last))
    if health.get("free") is not None and health["free"] < 1024 ** 3:
        found.append("The backup server has only %s free." % system.human_bytes(health["free"]))
    if current.get("last_error"):
        found.append("The last remote backup attempt failed: %s" % current["last_error"])
    return found
