"""Tests for remote backup, backup comparison and merge, and catalog additions."""

import os
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_app import DS, client, conn, new_device, part_id, unlock  # noqa: E402,F401

import pytest  # noqa: E402

import app as benchlog  # noqa: E402
import catalog  # noqa: E402
import db as dbm  # noqa: E402
import remote  # noqa: E402
import system  # noqa: E402


# ------------------------------------------------------------------ catalog

def test_gulikit_upgrades_are_in_the_catalog(client):
    with conn() as c:
        rows = c.execute(
            "SELECT t.name AS type_name, p.name, p.part_number, p.is_upgrade, p.default_cost FROM parts p"
            " JOIN device_types t ON t.id = p.device_type_id WHERE p.name LIKE 'GuliKit%'").fetchall()
    types = {r["type_name"] for r in rows}
    assert {"DualSense (PS5 controller)", "Joy-Con (L)", "Joy-Con (R)", "Steam Deck LCD", "Steam Deck OLED",
            "Nintendo Switch Lite", "Xbox Series controller", "Switch Pro Controller"} <= types
    assert all(r["is_upgrade"] == 1 and r["default_cost"] > 0 for r in rows)
    assert not any("Joy-Con 2" in t for t in types)  # no verified GuliKit part for the Switch 2 yet
    deck = next(r for r in rows if r["type_name"] == "Steam Deck LCD")
    assert deck["part_number"] == "SD02"
    page = client.get("/catalog?q=gulikit").get_data(as_text=True)
    assert "upgrade</span>" in page and "google.com/search?q=NS40T+GuliKit+TMR+stick" in page
    code = new_device(client, DS)
    assert "Upgrades and mods" in client.get("/d/" + code).get_data(as_text=True)


def test_new_catalog_parts_reach_an_existing_database(tmp_path, monkeypatch):
    path = str(tmp_path / "existing.db")
    c = dbm.connect(path)
    dbm.init(c)
    type_id = c.execute("SELECT id FROM device_types WHERE name = ?", (DS,)).fetchone()[0]
    # Simulate an older install: no GuliKit parts, and a cost the owner has edited.
    c.execute("DELETE FROM parts WHERE name LIKE 'GuliKit%'")
    c.execute("UPDATE parts SET default_cost = 7.77 WHERE device_type_id = ? AND name = 'Battery'", (type_id,))
    c.commit()
    before = c.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    dbm.init(c)  # what happens at startup after an update
    after = c.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    assert after > before
    assert c.execute("SELECT COUNT(*) FROM parts WHERE device_type_id = ? AND name LIKE 'GuliKit%'",
                     (type_id,)).fetchone()[0] == 2
    assert c.execute("SELECT default_cost FROM parts WHERE device_type_id = ? AND name = 'Battery'",
                     (type_id,)).fetchone()[0] == 7.77  # edits are kept
    linked = c.execute(
        "SELECT COUNT(*) FROM part_functions pf JOIN parts p ON p.id = pf.part_id"
        " WHERE p.device_type_id = ? AND p.name LIKE 'GuliKit%'", (type_id,)).fetchone()[0]
    assert linked == 4
    dbm.init(c)  # running it again adds nothing
    assert c.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == after
    c.close()


# -------------------------------------------------------- compare and merge

def test_compare_and_merge_bring_back_only_what_is_missing(client):
    unlock(client)
    kept = new_device(client, DS, serial="KEPT")
    deleted = new_device(client, DS, serial="DELETED-BY-MISTAKE")
    client.post("/d/%s/repair" % deleted, data={"part_id": part_id(DS, "Battery"), "source": "purchased", "cost": "9"})
    client.post("/d/%s/timer" % deleted, data={"action": "add", "minutes": "20"})
    backup = system.create_backup("manual")

    # After the backup: one device is deleted by mistake, one is edited, one is new.
    client.post("/d/%s/delete" % deleted, data={"confirm": deleted})
    client.post("/d/%s/update" % kept, data={"serial": "KEPT-EDITED", "notes": "edited after backup"})
    newer = new_device(client, DS, serial="NEW-SINCE-BACKUP")

    rows = {r["table"]: r for r in system.compare_backup(backup)}
    assert rows["devices"]["only_backup"] == 1 and rows["devices"]["backup_samples"] == [deleted]
    assert rows["devices"]["only_live"] == 1 and rows["devices"]["live_samples"] == [newer]
    assert rows["devices"]["changed"] >= 1
    assert rows["repair_items"]["only_backup"] == 1 and rows["work_sessions"]["only_backup"] == 1
    page = client.get("/system/backups/%s/compare" % backup).get_data(as_text=True)
    assert deleted in page and newer in page and "Bring back" in page

    r = client.post("/system/backups/%s/compare" % backup, data={"action": "merge"}, follow_redirects=True)
    assert "Brought back" in r.get_data(as_text=True)
    with conn() as c:
        assert c.execute("SELECT serial FROM devices WHERE code = ?", (deleted,)).fetchone()["serial"] == \
            "DELETED-BY-MISTAKE"
        # Current data is untouched: the edit and the new device are both still there.
        assert c.execute("SELECT serial FROM devices WHERE code = ?", (kept,)).fetchone()["serial"] == "KEPT-EDITED"
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (newer,)).fetchone()
        did = c.execute("SELECT id FROM devices WHERE code = ?", (deleted,)).fetchone()["id"]
        assert c.execute("SELECT COUNT(*) FROM repair_items WHERE device_id = ?", (did,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM work_sessions WHERE device_id = ?", (did,)).fetchone()[0] == 1
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert system.compare_backup(backup) and {r["table"]: r for r in system.compare_backup(backup)}[
        "devices"]["only_backup"] == 0
    # New IDs still never collide with restored ones.
    assert new_device(client, DS) not in (kept, deleted, newer)


def test_full_restore_from_compare_needs_confirmation(client):
    unlock(client)
    backup = system.create_backup("manual")
    newer = new_device(client, DS)
    client.post("/system/backups/%s/compare" % backup, data={"action": "restore", "confirm": "no"})
    with conn() as c:
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (newer,)).fetchone()
    client.post("/system/backups/%s/compare" % backup, data={"action": "restore", "confirm": "RESTORE"})
    with conn() as c:
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (newer,)).fetchone() is None


# ------------------------------------------------------------ remote: units

GOOD = {"host": "203.0.113.10", "user": "Backup", "port": 22, "directory": "~/benchlog-backups",
        "interval_days": 3, "keep": 30}


def test_remote_settings_validation():
    assert remote.validate(GOOD) == []
    assert remote.validate(dict(GOOD, host="backup.example.com")) == []
    for bad in [dict(GOOD, host="-oProxyCommand=evil"), dict(GOOD, host="a b"), dict(GOOD, host=""),
                dict(GOOD, user="Backup; rm -rf /"), dict(GOOD, user="-x"), dict(GOOD, directory="../../etc"),
                dict(GOOD, directory="-rf"), dict(GOOD, directory="back ups"), dict(GOOD, directory="$(reboot)"),
                dict(GOOD, port=0), dict(GOOD, port="abc"), dict(GOOD, interval_days=0), dict(GOOD, keep=1)]:
        assert remote.validate(bad), bad


def test_remote_folder_is_quoted_for_the_server_shell():
    assert remote._remote_dir(dict(GOOD, directory="~/benchlog-backups")) == '"$HOME"/benchlog-backups'
    assert remote._remote_dir(dict(GOOD, directory="/srv/backups/bench")) == "/srv/backups/bench"
    assert remote._remote_dir(dict(GOOD, directory="~")) == '"$HOME"'


def test_listing_parser_only_accepts_snapshots():
    text = """total 380
-rw------- 1 Backup Backup 180224 Oct  6 15:10 benchlog-20261006-151002.db
-rw------- 1 Backup Backup 176128 Oct  3 03:00 benchlog-20261003-030001.db
-rw------- 1 Backup Backup    512 Oct  6 15:10 benchlog-20261006-151002.db.part
drwx------ 3 Backup Backup   4096 Oct  6 15:10 photos
-rw-r--r-- 1 Backup Backup     99 Oct  1 00:00 notes.txt
"""
    rows = remote.parse_listing(text)
    assert [r["name"] for r in rows] == ["benchlog-20261006-151002.db", "benchlog-20261003-030001.db"]
    assert rows[0]["size"] == 180224 and time.strftime("%Y-%m-%d %H:%M", time.localtime(rows[0]["when"])) == \
        "2026-10-06 15:10"


def test_friendly_errors():
    assert "refused the key" in remote.friendly_error("Backup@203.0.113.10: Permission denied (publickey).")
    assert "could not be reached" in remote.friendly_error("ssh: connect to host 203.0.113.10 port 22: Connection timed out")
    assert "identity changed" in remote.friendly_error("Host key verification failed.")


def test_schedule_and_warnings(monkeypatch):
    remote.save_config(dict(GOOD, enabled=True))
    monkeypatch.setattr(remote, "has_key", lambda: True)
    system.save_json("remote-state.json", {})
    assert remote.is_due()  # never backed up
    assert any("no backup has reached" in w for w in remote.warnings())
    system.save_json("remote-state.json", {"last_backup_at": int(time.time()) - 86400,
                                           "check": {"reachable": True, "free": 50 * 1024 ** 3}})
    assert not remote.is_due() and remote.warnings() == []
    system.save_json("remote-state.json", {"last_backup_at": int(time.time()) - 6 * 86400,
                                           "check": {"reachable": False, "error": "The server could not be reached."}})
    assert remote.is_due()
    found = remote.warnings()
    assert any("could not be reached" in w for w in found) and any("ago" in w for w in found)
    remote.save_config(dict(GOOD, enabled=False))
    assert not remote.is_due() and remote.warnings() == []
    system.save_json("remote-state.json", {})


def test_remote_actions_need_the_admin_unlock(client):
    client.post("/system/admin", data={"action": "lock"})
    r = client.post("/system/remote", data={"action": "backup"})
    assert r.status_code == 302 and "/system#admin" in r.headers["Location"]
    unlock(client)
    r = client.post("/system/remote", data={"action": "save", "host": "bad host", "user": "Backup"},
                    follow_redirects=True)
    assert "host name or IP address" in r.get_data(as_text=True)


# ------------------------------------------------- remote: real SSH round trip

needs_ssh = pytest.mark.skipif(
    not (shutil.which("sshd") and shutil.which("ssh") and shutil.which("ssh-keygen")) or os.geteuid() != 0,
    reason="needs a local sshd and root")


@pytest.fixture()
def ssh_server(tmp_path):
    """A throwaway SSH server on localhost that accepts only the app's backup key."""
    remote.forget_host()
    for path in (remote.key_path(), remote.key_path() + ".pub"):
        if os.path.exists(path):
            os.remove(path)
    remote.ensure_key()
    host_key, auth = tmp_path / "host_key", tmp_path / "authorized_keys"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True)
    auth.write_text(remote.public_key() + "\n")
    os.chmod(auth, 0o600)
    os.makedirs("/run/sshd", exist_ok=True)
    port = 22022
    conf = tmp_path / "sshd_config"
    conf.write_text("Port %d\nListenAddress 127.0.0.1\nHostKey %s\nAuthorizedKeysFile %s\nPermitRootLogin yes\n"
                    "PasswordAuthentication no\nStrictModes no\nUsePAM no\nPidFile %s\n"
                    % (port, host_key, auth, tmp_path / "sshd.pid"))
    proc = subprocess.Popen([shutil.which("sshd"), "-D", "-e", "-f", str(conf)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    folder = tmp_path / "server" / "benchlog-backups"
    cfg = {"enabled": True, "host": "127.0.0.1", "user": "root", "port": port, "directory": str(folder),
           "interval_days": 3, "keep": 2}
    assert remote.save_config(cfg) == []
    yield remote.config(), folder
    proc.terminate()
    proc.wait(timeout=5)
    remote.save_config(dict(cfg, enabled=False))
    system.save_json("remote-state.json", {})


@needs_ssh
def test_remote_backup_round_trip(client, ssh_server):
    cfg, folder = ssh_server
    unlock(client)
    health = remote.check(cfg)
    assert health["reachable"], health["error"]
    assert health["free"] > 0 and health["snapshots"] == [] and health["path"] == str(folder)

    code = new_device(client, DS, serial="ON-THE-SERVER")
    photo_dir = os.path.join(system.DATA_DIR, "photos", code)
    os.makedirs(photo_dir, exist_ok=True)
    with open(os.path.join(photo_dir, "intake.jpg"), "wb") as fh:
        fh.write(b"\xff\xd8 fake jpeg bytes")

    log = []
    name = remote.push(log.append, cfg)
    assert remote.SNAPSHOT_NAME.match(name) and (folder / name).exists()
    assert not list(folder.glob("*.part"))
    assert (folder / "photos" / code / "intake.jpg").read_bytes() == b"\xff\xd8 fake jpeg bytes"
    assert remote.file_hash(str(folder / name)) and "Uploaded and verified." in log
    state = remote.state()
    assert state["last_backup_name"] == name and state["last_error"] == "" and len(state["check"]["snapshots"]) == 1
    assert not remote.is_due(cfg)

    # Lose data locally: a device and a photo file.
    client.post("/d/%s/delete" % code, data={"confirm": code})
    os.remove(os.path.join(photo_dir, "intake.jpg"))

    # Pick the snapshot from the list, download it, compare, and bring back what is missing.
    r = client.post("/system/remote", data={"action": "fetch", "name": name})
    assert r.status_code == 302 and "/compare" in r.headers["Location"]
    local = r.headers["Location"].split("/backups/")[1].split("/compare")[0]
    assert local.startswith("fetched-") and system.backup_path(local)
    assert code in client.get(r.headers["Location"]).get_data(as_text=True)
    client.post("/system/backups/%s/compare" % local, data={"action": "merge"})
    with conn() as c:
        assert c.execute("SELECT serial FROM devices WHERE code = ?", (code,)).fetchone()["serial"] == "ON-THE-SERVER"
    remote.sync_photos(log.append, cfg, remote.check(cfg), direction="down")
    assert open(os.path.join(photo_dir, "intake.jpg"), "rb").read() == b"\xff\xd8 fake jpeg bytes"

    # Older copies are removed past the keep limit, newest kept, and photos are never removed.
    time.sleep(1.1)
    second = remote.push(log.append, cfg)
    time.sleep(1.1)
    third = remote.push(log.append, cfg)
    remaining = sorted(p.name for p in folder.glob("benchlog-*.db"))
    assert remaining == sorted([second, third]) and (folder / "photos" / code / "intake.jpg").exists()

    # The System page shows the server state.
    page = client.get("/system").get_data(as_text=True)
    assert "Reachable" in page and third in page and "Download and compare" in page


@needs_ssh
def test_remote_refuses_a_server_that_changed_identity_or_key(client, ssh_server, tmp_path):
    cfg, folder = ssh_server
    assert remote.check(cfg)["reachable"]
    # A different key is not accepted by the server.
    os.rename(remote.key_path(), remote.key_path() + ".real")
    os.rename(remote.key_path() + ".pub", remote.key_path() + ".pub.real")
    try:
        remote.ensure_key()
        bad = remote.check(cfg)
        assert not bad["reachable"] and "refused the key" in bad["error"]
        with pytest.raises(RuntimeError):
            remote.push(lambda text: None, cfg)
        assert "refused the key" in remote.state()["last_error"]
    finally:
        os.replace(remote.key_path() + ".real", remote.key_path())
        os.replace(remote.key_path() + ".pub.real", remote.key_path() + ".pub")
    # An unreachable port gives a clear message instead of hanging.
    down = remote.check(dict(cfg, port=22023))
    assert not down["reachable"] and down["error"]


@needs_ssh
def test_photos_copy_without_rsync(client, ssh_server):
    """When rsync is missing on either machine, photos still travel both ways through tar."""
    cfg, folder = ssh_server
    health = dict(remote.check(cfg), rsync=False)
    photo_dir = os.path.join(system.DATA_DIR, "photos", "D-TAR")
    os.makedirs(photo_dir, exist_ok=True)
    with open(os.path.join(photo_dir, "a.jpg"), "wb") as fh:
        fh.write(b"tar path")
    log = []
    assert remote.sync_photos(log.append, cfg, health, direction="up") == "Copied with tar."
    assert (folder / "photos" / "D-TAR" / "a.jpg").read_bytes() == b"tar path"
    shutil.rmtree(photo_dir)
    remote.sync_photos(log.append, cfg, health, direction="down")
    assert open(os.path.join(photo_dir, "a.jpg"), "rb").read() == b"tar path"
    shutil.rmtree(photo_dir)
