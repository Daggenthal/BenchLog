"""End-to-end tests using Flask's test client and a throwaway data folder."""

import os
import sys
import tempfile

os.environ["BENCHLOG_DATA"] = tempfile.mkdtemp(prefix="benchlog-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import app as benchlog  # noqa: E402
import catalog  # noqa: E402
import db as dbm  # noqa: E402


@pytest.fixture()
def client():
    benchlog.app.config["TESTING"] = True
    with benchlog.app.test_client() as c:
        yield c


def conn():
    return dbm.connect(benchlog.DB_PATH)


def type_id(name):
    with conn() as c:
        return c.execute("SELECT id FROM device_types WHERE name = ?", (name,)).fetchone()["id"]


def part_id(type_name, part_name):
    with conn() as c:
        return c.execute(
            "SELECT p.id FROM parts p JOIN device_types t ON t.id = p.device_type_id"
            " WHERE t.name = ? AND p.name = ?", (type_name, part_name)).fetchone()["id"]


def function_id(type_name, fname):
    with conn() as c:
        return c.execute(
            "SELECT f.id FROM functions f JOIN device_types t ON t.id = f.device_type_id"
            " WHERE t.name = ? AND f.name = ?", (type_name, fname)).fetchone()["id"]


def device_row(code):
    with conn() as c:
        return c.execute("SELECT * FROM devices WHERE code = ?", (code,)).fetchone()


def new_device(client, type_name, **extra):
    data = {"device_type_id": type_id(type_name), "quantity": 1}
    data.update(extra)
    r = client.post("/devices/new", data=data)
    assert r.status_code == 302
    return r.headers["Location"].rsplit("/", 1)[1]


def set_seconds(code, seconds):
    """Give a device exactly one finished session of the given length."""
    with conn() as c:
        did = c.execute("SELECT id FROM devices WHERE code = ?", (code,)).fetchone()["id"]
        c.execute("DELETE FROM work_sessions WHERE device_id = ?", (did,))
        c.execute("INSERT INTO work_sessions (device_id, started_at, ended_at) VALUES (?, ?, ?)",
                  (did, 1_000_000, 1_000_000 + seconds))
        c.commit()


DS = "DualSense (PS5 controller)"
ADMIN = "bench-admin-pw"


def unlock(client):
    import system
    if not system.admin_is_set():
        system.set_admin(ADMIN)
    r = client.post("/system/admin", data={"action": "unlock", "admin_password": ADMIN})
    assert r.status_code == 302


def test_catalog_is_consistent():
    assert catalog.validate() == []
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM device_types").fetchone()[0] == len(catalog.CATALOG)


def test_pages_render(client):
    for path in ["/", "/devices", "/devices/new", "/boxes", "/lots", "/catalog", "/reports", "/settings",
                 "/catalog?q=drift", "/devices?status=all"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/d/D-9999").status_code == 404


def test_lot_splits_cost_and_creates_devices(client):
    r = client.post("/lots", data={"name": "Ten controllers", "total_cost": "100", "unit_count": "10"})
    lot_code = r.headers["Location"].rsplit("/", 1)[1]
    with conn() as c:
        lot_id = c.execute("SELECT id FROM lots WHERE code = ?", (lot_code,)).fetchone()["id"]
    r = client.post("/devices/new", data={"device_type_id": type_id(DS), "quantity": 10, "lot_id": lot_id})
    assert "/labels?d=" in r.headers["Location"]
    with conn() as c:
        codes = [row["code"] for row in c.execute("SELECT code FROM devices WHERE lot_id = ?", (lot_id,))]
    assert len(codes) == 10
    page = client.get("/d/" + codes[0]).get_data(as_text=True)
    assert "$10.00" in page  # 100 split across 10
    assert client.get("/l/" + lot_code).status_code == 200
    assert client.get("/labels?d=" + ",".join(codes)).status_code == 200


def test_intake_checklist_and_status(client):
    code = new_device(client, DS)
    drift = function_id(DS, "Left stick (no drift)")
    power = function_id(DS, "Powers on")
    r = client.post("/d/%s/tests" % code, data={"f_%d" % drift: "failed", "f_%d" % power: "working"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json() == {"ok": True, "failed": 1, "working": 1, "total": len(catalog.DUALSENSE["functions"])}
    client.post("/d/%s/intake-done" % code)
    assert device_row(code)["status"] == "needs_repair"
    # After intake, a change only updates the current state; the intake record is kept.
    client.post("/d/%s/tests" % code, data={"f_%d" % drift: "working"})
    with conn() as c:
        row = c.execute("SELECT intake, current FROM device_tests WHERE function_id = ?", (drift,)).fetchone()
    assert (row["intake"], row["current"]) == ("failed", "working")
    assert "At intake: failed" in client.get("/d/" + code).get_data(as_text=True)


def test_all_working_intake_marks_ready(client):
    code = new_device(client, DS)
    client.post("/d/%s/tests/all-working" % code)
    client.post("/d/%s/intake-done" % code)
    assert device_row(code)["status"] == "ready"


def test_only_one_timer_runs(client):
    a, b = new_device(client, DS), new_device(client, DS)
    client.post("/d/%s/timer" % a, data={"action": "start"})
    client.post("/d/%s/timer" % b, data={"action": "start"})
    with conn() as c:
        running = c.execute(
            "SELECT d.code FROM work_sessions s JOIN devices d ON d.id = s.device_id WHERE s.ended_at IS NULL"
        ).fetchall()
    assert [r["code"] for r in running] == [b]
    assert "Timer running on" in client.get("/").get_data(as_text=True)
    client.post("/d/%s/timer" % b, data={"action": "pause"})
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM work_sessions WHERE ended_at IS NULL").fetchone()[0] == 0
    # Marking repaired also stops a running timer.
    client.post("/d/%s/timer" % a, data={"action": "start"})
    client.post("/d/%s/status" % a, data={"status": "ready"})
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM work_sessions WHERE ended_at IS NULL").fetchone()[0] == 0


def test_session_edit_and_manual_time(client):
    code = new_device(client, DS)
    client.post("/d/%s/timer" % code, data={"action": "add", "minutes": "30"})
    with conn() as c:
        s = c.execute("SELECT s.* FROM work_sessions s JOIN devices d ON d.id = s.device_id WHERE d.code = ?",
                      (code,)).fetchone()
    assert s["ended_at"] - s["started_at"] == 1800
    client.post("/session/%d/edit" % s["id"], data={"minutes": "12"})
    with conn() as c:
        s2 = c.execute("SELECT * FROM work_sessions WHERE id = ?", (s["id"],)).fetchone()
    assert s2["ended_at"] - s2["started_at"] == 720
    client.post("/session/%d/edit" % s["id"], data={"delete": "1"})
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM work_sessions WHERE id = ?", (s["id"],)).fetchone()[0] == 0


def test_profit_and_hourly_rate_example(client):
    """The worked example: $10 unit, $4 part, sold $45 with $12 fees and shipping, 40 minutes."""
    code = new_device(client, DS, manual_cost="10")
    client.post("/d/%s/repair" % code, data={
        "part_id": part_id(DS, "Analog stick module (left)"), "source": "purchased", "cost": "4"})
    set_seconds(code, 40 * 60)
    client.post("/d/%s/sell" % code, data={"sale_price": "45", "sale_fees": "7", "sale_shipping": "5"})
    assert device_row(code)["status"] == "sold"
    with benchlog.app.test_request_context():
        d = benchlog.device_or_404(code)
        fin = benchlog.financials(d)
    assert fin["profit"] == pytest.approx(19.0)
    assert fin["hourly"] == pytest.approx(28.5)
    assert fin["after_labour"] == pytest.approx(19.0 - 25 * (40 / 60))  # target rate defaults to 25
    page = client.get("/d/" + code).get_data(as_text=True)
    assert "$28.50" in page and "$19.00" in page


def test_expected_price_gives_estimate(client):
    code = new_device(client, DS, manual_cost="10")
    set_seconds(code, 3600)
    client.post("/d/%s/money" % code, data={"expected_price": "40"})
    with benchlog.app.test_request_context():
        fin = benchlog.financials(benchlog.device_or_404(code))
    assert fin["estimate"] is True and fin["profit"] == pytest.approx(30.0) and fin["hourly"] == pytest.approx(30.0)


def test_donor_harvest_both_directions(client):
    donor = new_device(client, DS)
    target = new_device(client, DS)
    # Donor: right stick failed, left stick fine.
    client.post("/d/%s/tests" % donor, data={
        "f_%d" % function_id(DS, "Right stick (no drift)"): "failed",
        "f_%d" % function_id(DS, "Left stick (no drift)"): "working",
        "f_%d" % function_id(DS, "L3 click"): "working"})
    client.post("/d/%s/status" % donor, data={"status": "parts"})
    left, right = part_id(DS, "Analog stick module (left)"), part_id(DS, "Analog stick module (right)")
    with conn() as c:
        states = {r["part_id"]: r["state"] for r in c.execute(
            "SELECT dp.part_id, dp.state FROM device_parts dp JOIN devices d ON d.id = dp.device_id"
            " WHERE d.code = ?", (donor,))}
    assert states[left] == "good" and states[right] == "suspect"

    # From the repair side: use the donor's left stick on the target.
    client.post("/d/%s/repair" % target, data={"part_id": left, "source": "donor", "donor_code": donor})
    with conn() as c:
        row = c.execute(
            "SELECT dp.state, u.code FROM device_parts dp JOIN devices d ON d.id = dp.device_id"
            " LEFT JOIN devices u ON u.id = dp.used_on_device_id WHERE d.code = ? AND dp.part_id = ?",
            (donor, left)).fetchone()
        item = c.execute("SELECT r.id, r.source FROM repair_items r JOIN devices d ON d.id = r.device_id"
                         " WHERE d.code = ?", (target,)).fetchone()
    assert (row["state"], row["code"]) == ("harvested", target)
    assert item["source"] == "donor"
    assert ("used on" in client.get("/d/" + donor).get_data(as_text=True))

    # The same part cannot be taken twice.
    client.post("/d/%s/repair" % target, data={"part_id": left, "source": "donor", "donor_code": donor})
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM repair_items r JOIN devices d ON d.id = r.device_id"
                         " WHERE d.code = ?", (target,)).fetchone()[0] == 1

    # Removing the repair step gives the part back.
    client.post("/repair/%d/delete" % item["id"])
    with conn() as c:
        state = c.execute("SELECT dp.state FROM device_parts dp JOIN devices d ON d.id = dp.device_id"
                          " WHERE d.code = ? AND dp.part_id = ?", (donor, left)).fetchone()["state"]
    assert state == "good"

    # From the donor side: take the battery for the target.
    battery = part_id(DS, "Battery")
    client.post("/d/%s/harvest" % donor, data={"part_id": battery, "target_code": target})
    with conn() as c:
        assert c.execute("SELECT COUNT(*) FROM repair_items r JOIN devices d ON d.id = r.device_id"
                         " WHERE d.code = ? AND r.part_id = ?", (target, battery)).fetchone()[0] == 1


def test_cross_type_harvest_matches_by_name(client):
    donor = new_device(client, "Joy-Con (L)")
    target = new_device(client, "Joy-Con (R)")
    client.post("/d/%s/status" % donor, data={"status": "parts"})
    client.post("/d/%s/repair" % target, data={
        "part_id": part_id("Joy-Con (R)", "Analog stick module"), "source": "donor", "donor_code": donor})
    with conn() as c:
        state = c.execute(
            "SELECT dp.state FROM device_parts dp JOIN devices d ON d.id = dp.device_id WHERE d.code = ?"
            " AND dp.part_id = ?", (donor, part_id("Joy-Con (L)", "Analog stick module"))).fetchone()["state"]
    assert state == "harvested"


def test_boxes_count_by_status(client):
    r = client.post("/boxes", data={"name": "PS5 controllers"})
    box_code = r.headers["Location"].rsplit("/", 1)[1]
    codes = [new_device(client, DS) for _ in range(3)]
    client.post("/b/%s/add" % box_code, data={"codes": "%s, %s %s" % (codes[0], codes[1], codes[2][2:])})
    client.post("/d/%s/status" % codes[0], data={"status": "ready"})
    client.post("/d/%s/status" % codes[1], data={"status": "parts"})
    with benchlog.app.test_request_context():
        with conn() as c:
            box_id = c.execute("SELECT id FROM boxes WHERE code = ?", (box_code,)).fetchone()["id"]
        counts = benchlog.box_counts(box_id)
    assert (counts["untested"], counts["ready"], counts["parts"]) == (1, 1, 1)
    r = client.get("/b/" + box_code)
    assert r.status_code == 200 and "last_box=" + box_code in r.headers.get("Set-Cookie", "")
    # Selling takes a device out of its box.
    client.post("/d/%s/sell" % codes[0], data={"sale_price": "30"})
    assert device_row(codes[0])["box_id"] is None


def test_labels_and_qr_link(client):
    code = new_device(client, DS)
    r = client.get("/label/d/%s.png" % code)
    assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"
    r = client.post("/boxes", data={"name": "Label test box"})
    box_code = r.headers["Location"].rsplit("/", 1)[1]
    r = client.get("/label/b/%s.png" % box_code)
    assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_search_and_reverse_part_lookup(client):
    code = new_device(client, DS, serial="SN-ABC-123")
    assert client.get("/search?q=" + code.lower()).headers["Location"].endswith("/d/" + code)
    page = client.get("/search?q=SN-ABC").get_data(as_text=True)
    assert code in page
    page = client.get("/search?q=M92T36").get_data(as_text=True)
    assert "USB-C power delivery IC" in page
    page = client.get("/catalog?q=drift").get_data(as_text=True)
    assert "Analog stick module" in page


def test_exports_and_backup(client):
    for name in ["devices", "repairs", "sessions"]:
        r = client.get("/export/%s.csv" % name)
        assert r.status_code == 200 and r.mimetype == "text/csv"
    # Backups hold everything, so they need the admin unlock.
    assert client.get("/backup").status_code == 302
    unlock(client)
    r = client.get("/backup")
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"


def test_delete_requires_typing_the_id(client):
    code = new_device(client, DS)
    client.post("/d/%s/delete" % code, data={"confirm": "nope"})
    assert device_row(code) is not None
    client.post("/d/%s/delete" % code, data={"confirm": code})
    assert device_row(code) is None
    # IDs are never reused.
    assert new_device(client, DS) != code


def test_reports_group_repair_types(client):
    code = new_device(client, DS, manual_cost="10")
    client.post("/d/%s/repair" % code, data={
        "part_id": part_id(DS, "USB-C charging port"), "source": "purchased", "cost": "2"})
    set_seconds(code, 50 * 60)
    client.post("/d/%s/status" % code, data={"status": "ready"})
    page = client.get("/reports").get_data(as_text=True)
    assert "USB-C charging port" in page and "50m" in page


def test_every_plain_page_renders_as_a_full_page(client):
    """Guards against a template losing its layout, which once broke Settings."""
    for path in ("/", "/devices", "/devices/new", "/boxes", "/lots", "/catalog", "/reports",
                 "/settings", "/system", "/repairs", "/tickets", "/tickets/new", "/customers", "/labels"):
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert "<nav" in html and html.count("<h1") == 1, path
        assert len(html) < 200000, path
    page = client.get("/settings").get_data(as_text=True)
    assert "Save settings" in page and page.count("Open System") == 1
