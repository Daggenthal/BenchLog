"""Tests for part links, price lookup, the System page, backups, jobs, and self-update."""

import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_app import DS, client, conn, new_device, part_id, unlock  # noqa: E402,F401

import pytest  # noqa: E402

import app as benchlog  # noqa: E402
import db as dbm  # noqa: E402
import pricing  # noqa: E402
import system  # noqa: E402


def wait_for(job, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if system.get_job(job["id"])["status"] != "running":
            return system.get_job(job["id"])
        time.sleep(0.05)
    raise AssertionError("job did not finish: " + system.job_log(job["id"]))


# ---------------------------------------------------------------- part links

def test_part_names_link_to_a_web_search(client):
    with conn() as c:
        tid = c.execute("SELECT id FROM device_types WHERE name = ?", (DS,)).fetchone()["id"]
    page = client.get("/catalog/%d" % tid).get_data(as_text=True)
    assert "https://www.google.com/search?q=DualSense+PS5+controller+Analog+stick+module+left+replacement" in page
    assert "tbm=shop" in page and "ebay.com/sch/i.html" in page and "LH_BIN=1" in page


def test_chip_search_uses_the_part_number():
    assert pricing.part_query("USB-C power delivery IC", "M92T36", "Nintendo Switch (original)") == \
        "M92T36 USB-C power delivery IC"
    assert pricing.part_query("HDMI encoder IC", "MN86471A / MN864729", "PlayStation 4 console").startswith("MN86471A ")
    assert "ebay.de" in pricing.links("Fan", "", "Steam Deck LCD", "EBAY_DE")["ebay"]


def test_part_page_edit_and_supplier_link(client):
    pid = part_id(DS, "Battery")
    assert client.get("/part/%d" % pid).status_code == 200
    client.post("/part/%d" % pid, data={"name": "Battery", "part_number": "LIP1708", "purpose": "Power",
                                        "default_cost": "11.50", "buy_url": "https://example.com/battery"})
    with conn() as c:
        row = c.execute("SELECT default_cost, buy_url FROM parts WHERE id = ?", (pid,)).fetchone()
    assert row["default_cost"] == 11.5 and row["buy_url"] == "https://example.com/battery"
    assert "Your supplier" in client.get("/part/%d" % pid).get_data(as_text=True)


# -------------------------------------------------------------- price lookup

FAKE_RESULTS = {"itemSummaries": [
    {"title": "DualSense battery LIP1708 new", "price": {"value": "9.00", "currency": "USD"},
     "shippingOptions": [{"shippingCost": {"value": "3.00", "currency": "USD"}}],
     "itemWebUrl": "https://www.ebay.com/itm/1", "condition": "New", "seller": {"username": "partsguy"}},
    {"title": "Battery for PS5 controller", "price": {"value": "14.00", "currency": "USD"},
     "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
     "itemWebUrl": "https://www.ebay.com/itm/2", "condition": "New"},
    {"title": "Screw only", "price": {"value": "1.00", "currency": "USD"}, "itemWebUrl": "https://www.ebay.com/itm/3"},
    {"title": "Broken listing with no price"},
]}


@pytest.fixture()
def fake_ebay(monkeypatch):
    calls = []

    def fake_http(request, timeout=20):
        calls.append(request)
        if request.full_url == pricing.TOKEN_URL:
            return {"access_token": "tok-123", "expires_in": 7200}
        return FAKE_RESULTS

    monkeypatch.setattr(pricing, "_http", fake_http)
    pricing._token.update(value="", expires=0, client_id="")
    return calls


def test_price_search_parses_and_sorts(fake_ebay):
    items = pricing.search("battery", "app-id", "cert-id", "EBAY_US")
    assert [i["total"] for i in items] == [1.0, 12.0, 14.0]
    summary = pricing.summarize(items)
    assert (summary["low"], summary["median"], summary["high"], summary["count"]) == (1.0, 12.0, 14.0, 3)
    token_request, search_request = fake_ebay
    assert token_request.get_header("Authorization").startswith("Basic ")
    assert search_request.get_header("Authorization") == "Bearer tok-123"
    assert search_request.get_header("X-ebay-c-marketplace-id") == "EBAY_US"
    assert "buyingOptions%3A%7BFIXED_PRICE%7D%2Cconditions%3A%7BNEW%7D" in search_request.full_url
    # The token is reused for the next search.
    pricing.search("battery", "app-id", "cert-id")
    assert len(fake_ebay) == 3


def test_price_check_route_suggests_but_does_not_overwrite(client, fake_ebay):
    pid = part_id(DS, "Speaker")
    # Without keys, it explains what to do instead of failing.
    system.set_secret("ebay", {})
    r = client.post("/part/%d/price" % pid, data={"query": "x"}, follow_redirects=True)
    assert "Add your eBay developer keys" in r.get_data(as_text=True)

    client.post("/settings", data={"ebay_client_id": "app-id", "ebay_client_secret": "cert-id",
                                   "ebay_marketplace": "EBAY_US"})
    assert system.get_secret("ebay") == {"client_id": "app-id", "client_secret": "cert-id"}
    # A blank secret keeps the saved one, and the secret is never sent back to the page.
    client.post("/settings", data={"ebay_client_id": "app-id", "ebay_client_secret": ""})
    assert system.get_secret("ebay")["client_secret"] == "cert-id"
    assert "cert-id" not in client.get("/settings").get_data(as_text=True)

    with conn() as c:
        before = c.execute("SELECT default_cost FROM parts WHERE id = ?", (pid,)).fetchone()["default_cost"]
    page = client.post("/part/%d/price" % pid, data={"query": "dualsense speaker"}).get_data(as_text=True)
    assert "Median of 3 listing(s)" in page and "$12.00" in page
    with conn() as c:
        row = c.execute("SELECT default_cost, last_price FROM parts WHERE id = ?", (pid,)).fetchone()
    assert row["default_cost"] == before and row["last_price"] == 12.0
    client.post("/part/%d" % pid, data={"use_price": "12.00"})
    with conn() as c:
        assert c.execute("SELECT default_cost FROM parts WHERE id = ?", (pid,)).fetchone()["default_cost"] == 12.0


def test_secrets_file_is_private():
    system.set_secret("ebay", {"client_id": "a", "client_secret": "b"})
    mode = os.stat(os.path.join(system.DATA_DIR, "secrets.json")).st_mode & 0o777
    assert mode == 0o600


# -------------------------------------------------------------------- health

def test_health_snapshot_has_the_basics():
    h = system.read_health()
    assert h["disks"] and h["disks"][0]["total"] > 0
    assert 0 <= h["disks"][0]["free_percent"] <= 100
    assert h["mem"]["total"] > 0 and "uptime" in h and h["cpu_count"] >= 1
    assert isinstance(h["warnings"], list)


def test_helpers():
    assert system.human_bytes(0) == "0 B"
    assert system.human_bytes(1536) == "1.5 KB"
    assert system.human_bytes(64 * 1024 ** 3) == "64.0 GB"
    assert system.human_duration(90061) == "1d 1h 1m"
    assert system.decode_throttled("throttled=0x0") == []
    assert system.decode_throttled("throttled=0x50005") == [
        "Under-voltage right now. Check the power supply", "Throttled right now",
        "Under-voltage has happened since boot", "Throttling has happened since boot"]
    assert system.decode_throttled("nonsense") is None


def test_parse_upgradable():
    text = """WARNING: apt does not have a stable CLI interface. Use with caution in scripts.

Listing...
curl/stable-security 8.14.1-2+deb13u1 arm64 [upgradable from: 8.14.1-2]
libssl3t64/stable 3.5.1-1+deb13u2 arm64 [upgradable from: 3.5.1-1]
"""
    assert system.parse_upgradable(text) == [
        {"name": "curl", "new": "8.14.1-2+deb13u1", "old": "8.14.1-2"},
        {"name": "libssl3t64", "new": "3.5.1-1+deb13u2", "old": "3.5.1-1"}]


def test_sudo_password_never_appears_in_the_command(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    argv, stdin_text = system.sudo_argv(["apt-get", "update"], "hunter2")
    assert "hunter2" not in argv and stdin_text == "hunter2\n" and argv[:2] == ["sudo", "-S"]
    argv, stdin_text = system.sudo_argv(["apt-get", "update"], None)
    assert argv == ["sudo", "-n", "apt-get", "update"] and stdin_text is None


# ---------------------------------------------------------------- admin lock

def test_system_actions_are_locked_until_unlocked(client, monkeypatch):
    rebooted = []
    monkeypatch.setattr(system, "reboot", lambda password: rebooted.append(password))
    monkeypatch.setattr(system, "sudo_passwordless", lambda: True)
    monkeypatch.setattr(system, "restart_app", lambda: rebooted.append("app"))
    client.post("/system/admin", data={"action": "lock"})
    page = client.get("/system").get_data(as_text=True)
    assert "Device health" in page and "Reboot the device" not in page
    for path in ["/system/reboot", "/system/restart", "/system/os/check", "/system/app/check", "/system/backups"]:
        r = client.post(path, data={"action": "create"})
        assert r.status_code == 302 and "/system#admin" in r.headers["Location"], path
    assert client.get("/system/full-backup.zip").status_code == 302
    assert rebooted == []

    if not system.admin_is_set():
        system.set_admin("bench-admin-pw")
    client.post("/system/admin", data={"action": "unlock", "admin_password": "wrong-password"})
    assert client.post("/system/reboot").status_code == 302
    assert rebooted == []

    unlock(client)
    assert "Reboot the device" in client.get("/system").get_data(as_text=True)
    r = client.post("/system/reboot")
    assert r.status_code == 200 and rebooted == [None] and "Rebooting the device" in r.get_data(as_text=True)


def test_admin_password_rules(client):
    assert system.check_admin("bench-admin-pw") or not system.admin_is_set()
    unlock(client)
    r = client.post("/system/admin", data={"action": "set", "current_password": "bench-admin-pw",
                                           "admin_password": "short", "confirm_password": "short"},
                    follow_redirects=True)
    assert "at least 8 characters" in r.get_data(as_text=True)
    assert system.check_admin("bench-admin-pw")
    assert not system.check_admin("") and not system.check_admin("bench-admin-pW")


def test_reboot_asks_for_account_password_when_sudo_needs_one(client, monkeypatch):
    rebooted = []
    monkeypatch.setattr(system, "reboot", lambda password: rebooted.append(password))
    monkeypatch.setattr(system, "sudo_passwordless", lambda: False)
    monkeypatch.setattr(system, "verify_sudo", lambda password: password == "pi-password")
    unlock(client)
    client.post("/system/reboot", data={})
    client.post("/system/reboot", data={"sudo_password": "nope"})
    assert rebooted == []
    client.post("/system/reboot", data={"sudo_password": "pi-password"})
    assert rebooted == ["pi-password"]


# ------------------------------------------------------------------- backups

def test_backup_restore_round_trip(client):
    unlock(client)
    code = new_device(client, DS, serial="KEEP-ME")
    client.post("/system/backups", data={"action": "create"})
    name = next(b["name"] for b in system.list_backups() if b["kind"] == "manual")
    lost = new_device(client, DS, serial="ADDED-AFTER-BACKUP")

    # Without typing RESTORE nothing happens.
    client.post("/system/backups", data={"action": "restore", "name": name, "confirm": "yes"})
    with conn() as c:
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (lost,)).fetchone()

    client.post("/system/backups", data={"action": "restore", "name": name, "confirm": "RESTORE"})
    with conn() as c:
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (code,)).fetchone()
        assert c.execute("SELECT 1 FROM devices WHERE code = ?", (lost,)).fetchone() is None
    # The state from just before the restore was kept, so the restore itself can be undone.
    saved = next(b for b in system.list_backups() if b["kind"] == "pre-restore")
    check = sqlite3.connect(os.path.join(system.DATA_DIR, "backups", saved["name"]))
    assert check.execute("SELECT 1 FROM devices WHERE code = ?", (lost,)).fetchone()
    check.close()

    r = client.get("/system/backups/" + name)
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"
    assert client.get("/system/backups/..%2Fsecrets.json").status_code == 404
    assert system.backup_path("../secrets.json") is None
    r = client.get("/system/full-backup.zip")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_old_backups_are_pruned():
    folder = os.path.join(system.DATA_DIR, "backups")
    for day in range(1, 21):
        path = os.path.join(folder, "auto-202501%02d-030000.db" % day)
        open(path, "w").close()
        os.utime(path, (1_700_000_000 + day * 86400,) * 2)
    system.prune_backups()
    autos = [b for b in system.list_backups() if b["kind"] == "auto"]
    names = {b["name"] for b in autos}
    assert len(autos) == system.KEEP_AUTO
    assert "auto-20250120-030000.db" in names and "auto-20250101-030000.db" not in names  # oldest go first
    for name in names:
        if name.startswith("auto-2025"):
            os.remove(os.path.join(folder, name))


def test_migration_adds_columns_to_an_old_database(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(dbm.SCHEMA)
    for column in ("buy_url", "last_price", "last_price_at", "last_price_query"):
        old.execute("ALTER TABLE parts DROP COLUMN %s" % column) if column in {
            r[1] for r in old.execute("PRAGMA table_info(parts)")} else None
    old.commit()
    old.close()
    fresh = dbm.connect(path)
    dbm.init(fresh)
    columns = {r[1] for r in fresh.execute("PRAGMA table_info(parts)")}
    assert {"buy_url", "last_price", "last_price_at", "last_price_query"} <= columns
    fresh.close()


# ---------------------------------------------------------------------- jobs

def test_job_runs_steps_and_logs(client):
    job = system.start_job("os-check", "Echo test", [
        system.Step("Say hello", argv=["sh", "-c", "echo hello from a step"]),
        system.Step("Python step", func=lambda log: log("python says hi")),
    ])
    assert job is not None
    done = wait_for(job)
    log = system.job_log(job["id"])
    assert done["status"] == "done" and "hello from a step" in log and "python says hi" in log
    assert client.get("/system/job/" + job["id"]).status_code == 200
    assert client.get("/system/job/%s.json" % job["id"]).get_json()["status"] == "done"
    assert client.get("/system/job/not-a-job").status_code == 404


def test_failed_step_stops_the_job_and_runs_cleanup():
    cleaned, reached = [], []
    job = system.start_job("os-check", "Failing test", [
        system.Step("Fail", argv=["sh", "-c", "echo about to fail; exit 3"]),
        system.Step("Never runs", func=lambda log: reached.append(1)),
    ], on_failure=lambda log: cleaned.append(1), on_success=lambda log: reached.append("success"))
    done = wait_for(job)
    assert done["status"] == "failed" and cleaned == [1] and reached == []
    assert "Exit code 3" in system.job_log(job["id"])


def test_sudo_password_is_not_written_to_the_log(monkeypatch):
    # Stand in for sudo with a command that just reads stdin, to prove the password goes in that way.
    monkeypatch.setattr(system, "sudo_argv", lambda argv, password: (["sh", "-c", "read p; echo got ${#p} chars"], password + "\n"))
    job = system.start_job("os-check", "Secret test", [
        system.Step("Privileged", argv=["apt-get", "update"], use_sudo=True, sudo_password="s3cret-pw")])
    wait_for(job)
    log = system.job_log(job["id"])
    assert "got 9 chars" in log and "s3cret-pw" not in log and "$ sudo apt-get update" in log


# --------------------------------------------------------------- self-update

def run(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"] + list(args), cwd=cwd,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def git_install(tmp_path, monkeypatch):
    """A fake 'origin' repository and an installed clone of it."""
    origin, install = tmp_path / "origin", tmp_path / "install"
    origin.mkdir()
    run(origin, "init", "-b", "main")
    (origin / "requirements.txt").write_text("")
    (origin / "version.txt").write_text("one\n")
    run(origin, "add", ".")
    run(origin, "commit", "-m", "First version")
    run(tmp_path, "clone", str(origin), str(install))
    monkeypatch.setattr(system, "APP_DIR", str(install))
    return origin, install


def head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_update_check_apply_and_rollback(git_install):
    origin, install = git_install
    first = head(install)
    assert system.check_app_update()["behind"] == 0

    (origin / "version.txt").write_text("two\n")
    run(origin, "commit", "-am", "Add part price lookup")
    result = system.check_app_update()
    assert result["behind"] == 1 and result["commits"][0]["subject"] == "Add part price lookup"
    assert "version.txt" in result["files"] and head(install) == first  # checking changes nothing

    restarts = []
    backups_before = len([b for b in system.list_backups() if b["kind"] == "pre-update"])
    steps, undo, restart = system.update_steps(lambda: restarts.append("restart"))
    job = system.start_job("app-update", "Update", steps, on_failure=undo, on_success=lambda log: restart(),
                           restarts=True)
    assert wait_for(job)["status"] == "done", system.job_log(job["id"])
    assert head(install) == head(origin) and (install / "version.txt").read_text() == "two\n"
    assert restarts == ["restart"]
    assert system.load_json("update-state.json")["previous"] == first
    assert len([b for b in system.list_backups() if b["kind"] == "pre-update"]) >= max(backups_before, 1)

    time.sleep(1.1)  # job ids are per second
    steps, restart = system.rollback_steps(lambda: restarts.append("restart"))
    job = system.start_job("app-rollback", "Rollback", steps, on_success=lambda log: restart(), restarts=True)
    assert wait_for(job)["status"] == "done", system.job_log(job["id"])
    assert head(install) == first and (install / "version.txt").read_text() == "one\n"
    assert system.load_json("update-state.json")["previous"] == ""


def test_failed_update_puts_the_code_back(git_install):
    origin, install = git_install
    first = head(install)
    (origin / "requirements.txt").write_text("this-package-does-not-exist-benchlog==9.9.9\n")
    run(origin, "commit", "-am", "Break requirements")
    assert system.check_app_update()["behind"] == 1
    restarts = []
    time.sleep(1.1)
    steps, undo, restart = system.update_steps(lambda: restarts.append("restart"))
    job = system.start_job("app-update", "Update", steps, on_failure=undo, on_success=lambda log: restart(),
                           restarts=True)
    assert wait_for(job, 120)["status"] == "failed"
    assert head(install) == first and restarts == []
    assert "Put the code back" in system.job_log(job["id"])


def test_update_refuses_when_not_a_git_install(client, tmp_path, monkeypatch):
    monkeypatch.setattr(system, "APP_DIR", str(tmp_path))
    assert system.app_version()["is_git"] is False
    assert "not installed with git" in system.check_app_update()["error"]
    unlock(client)
    r = client.post("/system/app/apply", follow_redirects=True)
    assert "not installed with git" in r.get_data(as_text=True)


def test_remote_address_hides_embedded_credentials(git_install):
    _origin, install = git_install
    run(install, "remote", "set-url", "origin", "https://someone:secret-token@github.com/Daggenthal/BenchLog.git")
    remote = system.app_version()["remote"]
    assert remote == "https://github.com/Daggenthal/BenchLog.git"
