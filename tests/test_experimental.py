"""Tests for the experimental features: history, customer tickets, encryption, alerts, printing."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_app import DS, client, conn, function_id, new_device, part_id, set_seconds, type_id, unlock  # noqa: E402,F401
from test_remote import needs_ssh, ssh_server  # noqa: E402,F401

import pytest  # noqa: E402

import alerts  # noqa: E402
import app as benchlog  # noqa: E402
import labels  # noqa: E402
import printing  # noqa: E402
import remote  # noqa: E402
import system  # noqa: E402


def events(code):
    with conn() as c:
        return [r["text"] for r in c.execute(
            "SELECT e.text FROM device_events e JOIN devices d ON d.id = e.device_id WHERE d.code = ? ORDER BY e.id",
            (code,))]


# ------------------------------------------------------------------ history

def test_device_history_records_what_happened(client):
    code = new_device(client, DS)
    drift = function_id(DS, "Left stick (no drift)")
    client.post("/d/%s/tests" % code, data={"f_%d" % drift: "failed"})
    client.post("/d/%s/intake-done" % code)
    client.post("/d/%s/repair" % code, data={"part_id": part_id(DS, "Analog stick module (left)"),
                                             "source": "purchased", "cost": "3"})
    client.post("/d/%s/tests" % code, data={"f_%d" % drift: "working"})
    client.post("/d/%s/update" % code, data={"serial": "SN-1", "notes": ""})
    client.post("/d/%s/status" % code, data={"status": "ready"})
    client.post("/d/%s/sell" % code, data={"sale_price": "40"})
    log = events(code)
    assert log[0] == "Logged in"
    assert "Intake finished: 0 working, 1 failed (Left stick (no drift))" in log
    assert "Status changed from Untested to Needs repair" in log
    assert "Repair logged: Analog stick module (left) (bought part)" in log
    assert "Left stick (no drift): working, was failed" in log
    assert "Edited serial number" in log
    assert "Status changed from Needs repair to Repaired" in log
    assert "Sold for $40.00" in log
    page = client.get("/d/" + code).get_data(as_text=True)
    assert "History" in page and "Sold for $40.00" in page


def test_checklist_taps_during_intake_do_not_flood_history(client):
    code = new_device(client, DS)
    client.post("/d/%s/tests/all-working" % code)
    assert events(code) == ["Logged in"]


# ------------------------------------------------------------------ tickets

def make_ticket(client, name, priority="standard", due="", customer_id="", **extra):
    data = {"device_type_id": type_id(DS), "name": name, "email": name.lower().replace(" ", ".") + "@example.com",
            "problem": "Left stick drifts", "priority": priority, "due_at": due, "customer_id": customer_id,
            "carrier_in": "USPS", "service_in": "Priority", "tracking_in": "9400"}
    data.update(extra)
    r = client.post("/tickets/new", data=data)
    assert r.status_code == 302, r.get_data(as_text=True)
    return r.headers["Location"].rsplit("/", 1)[1]


def ticket_row(code):
    with conn() as c:
        return c.execute(
            "SELECT t.*, d.code AS device_code, d.status AS device_status, d.customer_id AS device_customer"
            " FROM tickets t LEFT JOIN devices d ON d.id = t.device_id WHERE t.code = ?", (code,)).fetchone()


def test_ticket_creates_customer_and_device(client):
    code = make_ticket(client, "Ada Example")
    t = ticket_row(code)
    assert t["device_code"].startswith("D-") and t["device_customer"] == t["customer_id"]
    assert t["status"] == "received" and t["carrier_in"] == "USPS"
    with conn() as c:
        customer = c.execute("SELECT * FROM customers WHERE id = ?", (t["customer_id"],)).fetchone()
    assert customer["name"] == "Ada Example" and customer["code"].startswith("C-")
    assert "Ticket %s opened" % code in events(t["device_code"])

    page = client.get("/d/" + t["device_code"]).get_data(as_text=True)
    assert "Ticket " + code in page and "there is no sale" in page and "Mark as sold" not in page
    assert client.get("/t/" + code).status_code == 200
    assert client.get("/c/" + customer["code"]).status_code == 200
    assert client.get("/search?q=" + code).headers["Location"].endswith("/t/" + code)
    assert client.get("/search?q=" + customer["code"]).headers["Location"].endswith("/c/" + customer["code"])
    assert "Ada Example" in client.get("/search?q=ada+exam").get_data(as_text=True)
    assert "Ada Example" in client.get("/customers?q=ada").get_data(as_text=True)

    # A second ticket for the same customer reuses the customer record.
    second = make_ticket(client, "", customer_id=str(customer["id"]))
    assert ticket_row(second)["customer_id"] == customer["id"]
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM customers WHERE name = 'Ada Example'").fetchone()[0] == 1


def test_ticket_needs_a_customer_and_device_type(client):
    r = client.post("/tickets/new", data={"device_type_id": type_id(DS), "name": ""}, follow_redirects=True)
    assert "Choose an existing customer" in r.get_data(as_text=True)
    r = client.post("/tickets/new", data={"name": "No Device"}, follow_redirects=True)
    assert "Pick a device type" in r.get_data(as_text=True)


def test_express_jobs_jump_the_queue(client):
    with conn() as c:
        c.execute("UPDATE tickets SET status = 'cancelled'")
        c.commit()
    standard_old = make_ticket(client, "Standard Old", received_at="2026-09-01")
    standard_due = make_ticket(client, "Standard Due", received_at="2026-09-20", due="2026-10-08")
    express = make_ticket(client, "Express New", priority="express", received_at="2026-10-05")
    with benchlog.app.test_request_context():
        order = [t["code"] for t in benchlog.ticket_queue()]
    assert order == [express, standard_due, standard_old]
    client.get("/tickets")  # clears the "ticket created" notices so only the list is left
    page = client.get("/tickets").get_data(as_text=True)
    assert page.index(express) < page.index(standard_due) < page.index(standard_old) and "Express" in page
    assert express in client.get("/").get_data(as_text=True)  # the dashboard shows the queue too

    client.post("/t/" + standard_old, data={"status": "cancelled"})
    assert standard_old not in client.get("/tickets").get_data(as_text=True)
    assert standard_old in client.get("/tickets?show=all").get_data(as_text=True)


def test_ticket_money_shipping_and_report(client):
    code = make_ticket(client, "Grace Example", received_at="2026-10-01")
    device = ticket_row(code)["device_code"]
    drift = function_id(DS, "Left stick (no drift)")
    client.post("/d/%s/tests/all-working" % device)
    client.post("/d/%s/tests" % device, data={"f_%d" % drift: "failed"})
    client.post("/d/%s/intake-done" % device)
    client.post("/d/%s/repair" % device, data={"part_id": part_id(DS, "Analog stick module (left)"),
                                               "source": "purchased", "cost": "4"})
    client.post("/d/%s/tests" % device, data={"f_%d" % drift: "working"})
    set_seconds(device, 30 * 60)

    # A quote alone gives an expected figure.
    client.post("/t/" + code, data={"problem": "Left stick drifts", "priority": "express", "quote": "60"})
    with benchlog.app.test_request_context():
        fin = benchlog.financials(benchlog.device_or_404(device))
    assert fin["estimate"] and not fin["realised"] and fin["profit"] == pytest.approx(56.0)

    client.post("/t/" + code, data={"problem": "Left stick drifts", "priority": "express", "quote": "60",
                                    "charged": "60", "carrier_out": "UPS", "service_out": "Ground",
                                    "tracking_out": "1Z999", "shipping_out_cost": "8"})
    client.post("/t/" + code, data={"status": "shipped"})
    t = ticket_row(code)
    assert t["status"] == "shipped" and t["shipped_at"] and t["device_status"] == "sold" and t["priority"] == "express"
    with benchlog.app.test_request_context():
        fin = benchlog.financials(benchlog.device_or_404(device))
    # 60 charged, less 8 return shipping and a 4 part, for 30 minutes of work.
    assert fin["realised"] and fin["profit"] == pytest.approx(48.0) and fin["hourly"] == pytest.approx(96.0)

    page = client.get("/t/" + code).get_data(as_text=True)
    assert "$48.00" in page and "Turnaround, received to shipped" in page
    report = client.get("/t/%s/report" % code).get_data(as_text=True)
    assert "Left stick (no drift): failed" in report and "Analog stick module (left) (replaced)" in report
    assert "1Z999" in report and "$" not in report  # the customer copy carries no costs
    assert "Grace Example" not in report            # and no personal details beyond the repair ID
    assert "customer</span>" in client.get("/devices?status=all").get_data(as_text=True)
    assert "Ticket %s: Shipped" % code in events(device)


# --------------------------------------------------------------- encryption

def test_encryption_needs_a_passphrase():
    system.set_secret("remote_encryption", {})
    base = {"host": "203.0.113.10", "user": "Backup", "port": 22, "directory": "~/b", "interval_days": 3, "keep": 5}
    assert remote.save_config(dict(base, encrypt=True)) == ["Set an encryption passphrase before turning encryption on."]
    assert remote.set_passphrase("short") == "Use at least 12 characters for the passphrase."
    assert remote.set_passphrase("correct horse battery") == ""
    assert remote.save_config(dict(base, encrypt=True)) == []
    assert remote.config()["encrypt"] is True
    remote.save_config(dict(base, encrypt=False))
    assert remote.SNAPSHOT_NAME.match("benchlog-20261006-151002.db.enc").group(3) == ".enc"
    assert remote.parse_listing("-rw------- 1 a a 99 Oct 6 15:10 benchlog-20261006-151002.db.enc")[0]["encrypted"]


@needs_ssh
def test_encrypted_remote_backup_round_trip(client, ssh_server):
    cfg, folder = ssh_server
    unlock(client)
    assert remote.set_passphrase("correct horse battery staple") == ""
    assert remote.save_config(dict(cfg, encrypt=True)) == []
    cfg = remote.config()
    code = new_device(client, DS, serial="SECRET-SERIAL-123")
    log = []
    name = remote.push(log.append, cfg)
    assert name.endswith(".db.enc") and "Encrypted before sending." in log
    stored = (folder / name).read_bytes()
    assert not stored.startswith(b"SQLite format 3") and b"SECRET-SERIAL-123" not in stored
    assert not os.path.exists(os.path.join(system.DATA_DIR, "remote-upload.db"))
    assert not os.path.exists(os.path.join(system.DATA_DIR, "remote-upload.db.enc"))

    local = remote.fetch(name, cfg)
    assert open(system.backup_path(local), "rb").read(15) == b"SQLite format 3"
    assert {r["table"]: r for r in system.compare_backup(local)}["devices"]["only_backup"] == 0

    # The wrong passphrase cannot open it, and nothing half-written is left behind.
    remote.set_passphrase("a different passphrase entirely")
    os.remove(system.backup_path(local))
    with pytest.raises(RuntimeError, match="not the one it was encrypted with"):
        remote.fetch(name, cfg)
    assert system.backup_path(local) is None
    assert not [f for f in os.listdir(os.path.join(system.DATA_DIR, "backups")) if f.endswith((".part", ".enc"))]
    remote.save_config(dict(cfg, encrypt=False))


# ------------------------------------------------------------------- alerts

@pytest.fixture()
def fake_ntfy(monkeypatch):
    sent = []

    def fake_post(request, timeout=15):
        sent.append({"url": request.full_url, "title": request.get_header("Title"),
                     "body": request.data.decode("utf-8"), "priority": request.get_header("Priority")})
        return 200

    monkeypatch.setattr(alerts, "_post", fake_post)
    system.save_json("alerts-state.json", {})
    return sent


def test_alert_address_validation():
    assert alerts.save_config("https://ntfy.sh/benchlog-k3j4h5g6f7", True) == ""
    assert alerts.config() == {"enabled": True, "url": "https://ntfy.sh/benchlog-k3j4h5g6f7"}
    assert alerts.save_config("http://192.168.1.20:8080/bench_alerts", True) == ""
    for bad in ["ntfy.sh/topic", "https://ntfy.sh/", "https://ntfy.sh/a b", "ftp://ntfy.sh/topictopic",
                "https://ntfy.sh/short"]:
        assert alerts.save_config(bad, True), bad
    assert alerts.save_config("", True) == "" and alerts.config()["enabled"] is False


def test_alerts_fire_once_then_remind_then_report_resolved(fake_ntfy):
    alerts.save_config("https://ntfy.sh/benchlog-k3j4h5g6f7", True)
    now = 1_800_000_000
    full = "App data storage is almost full: 2.1 GB left."
    assert alerts.check([full, "The system runs from an SD card. Keep backups current."], now=now) == [full]
    assert len(fake_ntfy) == 1 and fake_ntfy[0]["priority"] == "high" and fake_ntfy[0]["body"] == full
    # The same problem an hour later, with a different number in it, is not sent again.
    assert alerts.check(["App data storage is almost full: 1.9 GB left."], now=now + 3600) == []
    # It is repeated after a few days if it is still there.
    assert len(alerts.check(["App data storage is almost full: 1.2 GB left."], now=now + 4 * 86400)) == 1
    # When it clears, one message says so.
    cleared = alerts.check([], now=now + 5 * 86400)
    assert cleared and cleared[0].startswith("resolved:") and fake_ntfy[-1]["title"] == "Bench Log: resolved"
    assert alerts.check([], now=now + 6 * 86400) == []
    # Nothing is sent while alerts are switched off.
    alerts.save_config("https://ntfy.sh/benchlog-k3j4h5g6f7", False)
    assert alerts.check([full], now=now + 7 * 86400) == []


def test_alert_test_button(client, fake_ntfy):
    unlock(client)
    r = client.post("/system/alerts", data={"url": "https://ntfy.sh/benchlog-k3j4h5g6f7", "enabled": "1",
                                            "action": "test"}, follow_redirects=True)
    assert "Test alert sent" in r.get_data(as_text=True)
    assert fake_ntfy[0]["url"] == "https://ntfy.sh/benchlog-k3j4h5g6f7" and fake_ntfy[0]["title"] == "Bench Log test"
    r = client.post("/system/alerts", data={"url": "not a url", "action": "save"}, follow_redirects=True)
    assert "should look like" in r.get_data(as_text=True)
    alerts.save_config("", False)


# ----------------------------------------------------------------- printing

needs_printer_library = pytest.mark.skipif(not printing.available(), reason="brother_ql is not installed")


@needs_printer_library
def test_labels_convert_to_printer_data():
    png = labels.device_label("http://pi.local:8080/d/D-0042", "D-0042", DS, "U", "2026-10-06").read()
    black = printing.raster(png, "QL-800", "62")
    red = printing.raster(png, "QL-800", "62red")
    assert isinstance(black, bytes) and len(black) > 1000 and len(red) > len(black)
    box = labels.box_label("http://pi.local:8080/b/B-01", "B-01", "PS5 controllers").read()
    assert len(printing.raster(box, "QL-820NWB", "62")) > 1000
    with pytest.raises(RuntimeError):
        printing.raster(png, "LaserJet", "62")
    assert printing.backend_for("tcp://192.168.1.50") == "network"
    assert printing.backend_for("usb://0x04f9:0x209b") == "pyusb"
    with pytest.raises(RuntimeError):
        printing.backend_for("/dev/usb/lp0")
    with pytest.raises(RuntimeError, match="No printer found"):
        printing.print_png(png, "QL-800", "file:///dev/does-not-exist", "62")


def test_print_buttons_and_settings(client, monkeypatch):
    jobs = []
    monkeypatch.setattr(printing, "available", lambda: True)
    monkeypatch.setattr(printing, "print_png", lambda png, model, address, tape: jobs.append(
        (png[:8], model, address, tape)))
    unlock(client)
    r = client.post("/system/printer", data={"printer_model": "QL-820NWB", "printer_address": "tcp://192.168.1.50",
                                             "printer_tape": "62red", "action": "test"}, follow_redirects=True)
    assert "Test label sent" in r.get_data(as_text=True)
    assert jobs == [(b"\x89PNG\r\n\x1a\n", "QL-820NWB", "tcp://192.168.1.50", "62red")]
    r = client.post("/system/printer", data={"printer_model": "QL-800", "printer_address": "lp0",
                                             "printer_tape": "62", "action": "save"}, follow_redirects=True)
    assert "should start with file://" in r.get_data(as_text=True)

    code = new_device(client, DS)
    assert "Print label" in client.get("/d/" + code).get_data(as_text=True)
    r = client.post("/print/d/" + code, follow_redirects=True)
    assert "Label sent to the printer" in r.get_data(as_text=True) and len(jobs) == 2
    assert "Label printed (U)" in events(code)

    def broken(png, model, address, tape):
        raise RuntimeError("No printer found at tcp://192.168.1.50.")

    monkeypatch.setattr(printing, "print_png", broken)
    r = client.post("/print/d/" + code, follow_redirects=True)
    assert "No printer found" in r.get_data(as_text=True)

    monkeypatch.setattr(printing, "available", lambda: False)
    page = client.get("/d/" + code).get_data(as_text=True)
    assert "Set up printing" in page and "Print label" not in page
    assert "Install printing support" in client.get("/system").get_data(as_text=True)


def test_new_pages_render(client):
    unlock(client)
    for path in ["/tickets", "/tickets?show=all", "/tickets/new", "/customers", "/system"]:
        assert client.get(path).status_code == 200, path
    page = client.get("/system").get_data(as_text=True)
    assert "Phone alerts" in page and "Label printer" in page and "Encryption" in page
