"""Bench Log: repair tracking, donor inventory, and QR labels for a one-person repair bench.

Run with:  python app.py
Data (database and photos) lives in ./data unless BENCHLOG_DATA is set.
"""

import csv
import io
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

import db as dbm
import labels
import pricing
import system

VERSION = "1.1.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BENCHLOG_DATA", os.path.join(BASE_DIR, "data"))
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
DB_PATH = os.path.join(DATA_DIR, "benchlog.db")
os.makedirs(PHOTO_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

_secret_path = os.path.join(DATA_DIR, "secret.key")
if not os.path.exists(_secret_path):
    with open(_secret_path, "w") as fh:
        fh.write(secrets.token_hex(32))
with open(_secret_path) as fh:
    app.secret_key = fh.read().strip()

# key: (label, letter printed on the sticker)
STATUSES = {
    "untested": ("Untested", "U"),
    "needs_repair": ("Needs repair", "N"),
    "ready": ("Repaired", "R"),
    "parts": ("Parts", "P"),
    "sold": ("Sold", "S"),
}
TEST_RESULTS = ("working", "failed", "untested")
PART_STATES = ("good", "suspect", "bad", "unknown", "harvested")
SOURCES = {
    "purchased": "Bought part",
    "donor": "From a donor",
    "in_place": "Repaired in place (no part)",
}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}

with dbm.connect(DB_PATH) as _conn:
    dbm.init(_conn)
system.configure(DATA_DIR, DB_PATH)


# ------------------------------------------------------------------ helpers

def now():
    return int(time.time())


def get_db():
    if "db" not in g:
        g.db = dbm.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def setting_float(key, default):
    try:
        return float(setting(key, default))
    except (TypeError, ValueError):
        return float(default)


def fnum(name, default=None):
    """Read an optional number from the submitted form."""
    raw = (request.form.get(name) or "").strip().replace(",", ".").lstrip("$")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def fint(name, default=None):
    value = fnum(name)
    return int(value) if value is not None else default


def base_url():
    configured = setting("base_url").strip().rstrip("/")
    return configured or request.url_root.rstrip("/")


def back(default_endpoint="index", **values):
    return redirect(request.form.get("next") or url_for(default_endpoint, **values))


DEVICE_SELECT = """
    SELECT d.*, t.name AS type_name, t.category AS category,
           b.code AS box_code, b.name AS box_name, l.code AS lot_code, l.name AS lot_name
    FROM devices d
    JOIN device_types t ON t.id = d.device_type_id
    LEFT JOIN boxes b ON b.id = d.box_id
    LEFT JOIN lots l ON l.id = d.lot_id
"""


def device_or_404(code):
    row = get_db().execute(DEVICE_SELECT + " WHERE d.code = ?", (code.strip().upper(),)).fetchone()
    if row is None:
        abort(404)
    return row


def touch(device_id):
    get_db().execute("UPDATE devices SET updated_at = ? WHERE id = ?", (now(), device_id))


def running_session():
    return get_db().execute(
        "SELECT s.*, d.code AS device_code FROM work_sessions s JOIN devices d ON d.id = s.device_id"
        " WHERE s.ended_at IS NULL ORDER BY s.started_at DESC LIMIT 1"
    ).fetchone()


def stop_timers(device_id=None):
    conn = get_db()
    if device_id is None:
        conn.execute("UPDATE work_sessions SET ended_at = ? WHERE ended_at IS NULL", (now(),))
    else:
        conn.execute(
            "UPDATE work_sessions SET ended_at = ? WHERE ended_at IS NULL AND device_id = ?",
            (now(), device_id),
        )


def worked_seconds(device_id):
    row = get_db().execute(
        "SELECT COALESCE(SUM(COALESCE(ended_at, ?) - started_at), 0) AS s FROM work_sessions"
        " WHERE device_id = ?",
        (now(), device_id),
    ).fetchone()
    return max(0, int(row["s"]))


def cost_basis(device):
    """What this unit cost to acquire: a manual figure, or its share of the lot."""
    if device["manual_cost"] is not None:
        return float(device["manual_cost"])
    if device["lot_id"]:
        lot = get_db().execute(
            "SELECT total_cost, unit_count,"
            " (SELECT COUNT(*) FROM devices WHERE lot_id = lots.id) AS n FROM lots WHERE id = ?",
            (device["lot_id"],),
        ).fetchone()
        if lot:
            units = max(lot["unit_count"] or 0, lot["n"] or 0)
            return float(lot["total_cost"]) / units if units else 0.0
    return 0.0


def parts_cost(device_id):
    row = get_db().execute(
        "SELECT COALESCE(SUM(cost), 0) AS c FROM repair_items WHERE device_id = ?", (device_id,)
    ).fetchone()
    return float(row["c"])


def financials(device):
    """Money and time for one device. Uses the real sale if sold, else the expected price."""
    basis = cost_basis(device)
    parts = parts_cost(device["id"])
    seconds = worked_seconds(device["id"])
    hours = seconds / 3600.0
    target = setting_float("target_rate", 25)

    net, estimate = None, False
    if device["status"] == "sold" and device["sale_price"] is not None:
        net = float(device["sale_price"]) - float(device["sale_fees"] or 0) - float(device["sale_shipping"] or 0)
    elif device["expected_price"] is not None:
        net, estimate = float(device["expected_price"]), True

    total_cost = basis + parts
    profit = net - total_cost if net is not None else None
    labour = hours * target
    return {
        "basis": basis,
        "parts": parts,
        "total_cost": total_cost,
        "net": net,
        "estimate": estimate,
        "profit": profit,
        "seconds": seconds,
        "hourly": (profit / hours) if profit is not None and seconds >= 60 else None,
        "target": target,
        "labour": labour,
        "after_labour": (profit - labour) if profit is not None else None,
    }


def build_donor_parts(device, overwrite=False):
    """Create the per-part state list for a donor from its checklist results.

    A part is 'suspect' if anything it is responsible for failed, 'good' if
    everything it is responsible for worked, otherwise 'unknown'.
    """
    conn = get_db()
    tests = {r["function_id"]: r["current"] for r in conn.execute(
        "SELECT function_id, current FROM device_tests WHERE device_id = ?", (device["id"],))}
    for part in conn.execute("SELECT id FROM parts WHERE device_type_id = ?", (device["device_type_id"],)):
        fids = [r["function_id"] for r in conn.execute(
            "SELECT function_id FROM part_functions WHERE part_id = ?", (part["id"],))]
        results = [tests.get(f, "untested") for f in fids]
        if "failed" in results:
            state = "suspect"
        elif results and all(r == "working" for r in results):
            state = "good"
        else:
            state = "unknown"
        existing = conn.execute(
            "SELECT state FROM device_parts WHERE device_id = ? AND part_id = ?", (device["id"], part["id"])
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO device_parts (device_id, part_id, state) VALUES (?, ?, ?)",
                (device["id"], part["id"], state),
            )
        elif overwrite and existing["state"] != "harvested":
            conn.execute(
                "UPDATE device_parts SET state = ? WHERE device_id = ? AND part_id = ?",
                (state, device["id"], part["id"]),
            )


def set_status(device, status):
    conn = get_db()
    if status not in STATUSES:
        abort(400)
    if status in ("ready", "parts", "sold"):
        stop_timers(device["id"])
    if status == "parts":
        build_donor_parts(device)
    if status == "sold":
        conn.execute("UPDATE devices SET box_id = NULL WHERE id = ?", (device["id"],))
    conn.execute(
        "UPDATE devices SET status = ?, updated_at = ? WHERE id = ?", (status, now(), device["id"])
    )


def create_device(type_id, lot_id=None, box_id=None, manual_cost=None, serial="", notes=""):
    conn = get_db()
    ts = now()
    cur = conn.execute(
        "INSERT INTO devices (device_type_id, lot_id, box_id, manual_cost, serial, notes, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (type_id, lot_id, box_id, manual_cost, serial, notes, ts, ts),
    )
    code = "D-%04d" % cur.lastrowid
    conn.execute("UPDATE devices SET code = ? WHERE id = ?", (code, cur.lastrowid))
    return code


def record_harvest(donor, donor_part, target, cost, note=""):
    """Move one part from a donor to a repair, keeping both sides of the record."""
    conn = get_db()
    match = conn.execute(
        "SELECT id FROM parts WHERE device_type_id = ? AND name = ?",
        (target["device_type_id"], donor_part["name"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO repair_items (device_id, part_id, description, source, donor_device_id, donor_part_id,"
        " cost, created_at) VALUES (?, ?, ?, 'donor', ?, ?, ?, ?)",
        (target["id"], match["id"] if match else None, note or ("" if match else donor_part["name"]),
         donor["id"], donor_part["id"], cost or 0, now()),
    )
    conn.execute(
        "INSERT INTO device_parts (device_id, part_id, state, used_on_device_id) VALUES (?, ?, 'harvested', ?)"
        " ON CONFLICT(device_id, part_id) DO UPDATE SET state = 'harvested', used_on_device_id = excluded.used_on_device_id",
        (donor["id"], donor_part["id"], target["id"]),
    )
    touch(donor["id"])
    touch(target["id"])


def grouped_types():
    groups = {}
    for t in get_db().execute("SELECT * FROM device_types ORDER BY sort, name"):
        groups.setdefault(t["category"], []).append(t)
    return groups


# ------------------------------------------------------------ template glue

@app.template_filter("money")
def money_filter(value):
    if value is None:
        return ""
    symbol = setting("currency", "$")
    sign = "-" if value < 0 else ""
    return "%s%s%.2f" % (sign, symbol, abs(value))


@app.template_filter("dur")
def duration_filter(seconds):
    seconds = int(seconds or 0)
    h, m = seconds // 3600, (seconds % 3600) // 60
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm" % m
    return "%ds" % seconds


@app.template_filter("dt")
def datetime_filter(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


@app.template_filter("date")
def date_filter(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


@app.context_processor
def inject_globals():
    return {"running": running_session(), "now_ts": now()}


# Globals, not context values, so the shared macros can see them too.
app.jinja_env.globals.update(STATUSES=STATUSES, SOURCES=SOURCES, PART_STATES=PART_STATES, VERSION=VERSION)


def part_links(name, number, type_name):
    """Search links for a part, for use in any template."""
    return pricing.links(name, number, type_name, setting("ebay_marketplace", "EBAY_US"))


app.jinja_env.globals.update(part_links=part_links)
app.jinja_env.filters["bytes"] = system.human_bytes


# ---------------------------------------------------------------- dashboard

@app.route("/")
def index():
    conn = get_db()
    counts = {key: 0 for key in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM devices GROUP BY status"):
        counts[row["status"]] = row["n"]
    queue = conn.execute(
        DEVICE_SELECT + " WHERE d.status = 'needs_repair' ORDER BY d.created_at LIMIT 12"
    ).fetchall()
    recent = conn.execute(DEVICE_SELECT + " ORDER BY d.updated_at DESC LIMIT 8").fetchall()
    return render_template("index.html", counts=counts, queue=queue, recent=recent)


@app.route("/search")
def search():
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    if not q:
        return redirect(url_for("index"))
    code = q.upper()
    if conn.execute("SELECT 1 FROM devices WHERE code = ?", (code,)).fetchone():
        return redirect(url_for("device", code=code))
    if conn.execute("SELECT 1 FROM boxes WHERE code = ?", (code,)).fetchone():
        return redirect(url_for("box", code=code))
    if conn.execute("SELECT 1 FROM lots WHERE code = ?", (code,)).fetchone():
        return redirect(url_for("lot", code=code))
    like = "%" + q + "%"
    devices = conn.execute(
        DEVICE_SELECT + " WHERE d.code LIKE ? OR d.serial LIKE ? OR d.notes LIKE ? OR t.name LIKE ?"
        " ORDER BY d.updated_at DESC LIMIT 100",
        (like, like, like, like),
    ).fetchall()
    return render_template("search.html", q=q, devices=devices, parts=find_parts(q))


def find_parts(q, type_id=None):
    """Reverse search: match part names, numbers, purposes, and the symptoms they fix."""
    conn = get_db()
    like = "%" + q + "%"
    sql = (
        "SELECT DISTINCT p.*, t.name AS type_name FROM parts p"
        " JOIN device_types t ON t.id = p.device_type_id"
        " LEFT JOIN part_functions pf ON pf.part_id = p.id"
        " LEFT JOIN functions f ON f.id = pf.function_id"
        " WHERE (p.name LIKE ? OR p.part_number LIKE ? OR p.purpose LIKE ? OR f.name LIKE ?)"
    )
    args = [like, like, like, like]
    if type_id:
        sql += " AND p.device_type_id = ?"
        args.append(type_id)
    sql += " ORDER BY t.sort, p.sort LIMIT 80"
    results = []
    for part in conn.execute(sql, args).fetchall():
        fixes = [r["name"] for r in conn.execute(
            "SELECT f.name FROM part_functions pf JOIN functions f ON f.id = pf.function_id"
            " WHERE pf.part_id = ? ORDER BY f.sort", (part["id"],))]
        donors = conn.execute(
            "SELECT d.code, dp.state FROM device_parts dp JOIN devices d ON d.id = dp.device_id"
            " WHERE dp.part_id = ? AND d.status = 'parts' AND dp.state IN ('good', 'unknown')"
            " ORDER BY dp.state, d.code", (part["id"],)).fetchall()
        results.append({"part": part, "fixes": fixes, "donors": donors})
    return results


# ------------------------------------------------------------------ devices

@app.route("/devices")
def devices():
    conn = get_db()
    status = request.args.get("status") or ""
    type_id = request.args.get("type") or ""
    sql, args = DEVICE_SELECT + " WHERE 1 = 1", []
    if status in STATUSES:
        sql += " AND d.status = ?"
        args.append(status)
    elif status != "all":
        sql += " AND d.status != 'sold'"
    if type_id.isdigit():
        sql += " AND d.device_type_id = ?"
        args.append(int(type_id))
    sql += " ORDER BY d.id DESC LIMIT 500"
    return render_template(
        "devices.html", devices=conn.execute(sql, args).fetchall(), status=status, type_id=type_id,
        types=conn.execute("SELECT * FROM device_types ORDER BY sort, name").fetchall(),
    )


@app.route("/devices/new", methods=["GET", "POST"])
def device_new():
    conn = get_db()
    if request.method == "POST":
        type_id = fint("device_type_id")
        if not type_id or not conn.execute("SELECT 1 FROM device_types WHERE id = ?", (type_id,)).fetchone():
            flash("Pick a device type.", "error")
            return redirect(url_for("device_new"))
        quantity = max(1, min(fint("quantity", 1), 100))
        lot_id = fint("lot_id")
        box_id = fint("box_id")
        codes = [
            create_device(
                type_id, lot_id=lot_id, box_id=box_id,
                manual_cost=None if lot_id else fnum("manual_cost"),
                serial=(request.form.get("serial") or "").strip() if quantity == 1 else "",
                notes=(request.form.get("notes") or "").strip(),
            )
            for _ in range(quantity)
        ]
        conn.commit()
        if quantity == 1:
            return redirect(url_for("device", code=codes[0]))
        flash("Created %d devices: %s to %s." % (quantity, codes[0], codes[-1]), "ok")
        return redirect(url_for("label_sheet", d=",".join(codes)))
    return render_template(
        "device_new.html", groups=grouped_types(),
        lots=conn.execute("SELECT * FROM lots ORDER BY id DESC").fetchall(),
        boxes=conn.execute("SELECT * FROM boxes ORDER BY code").fetchall(),
        preset_lot=request.args.get("lot", ""), preset_box=request.args.get("box", ""),
    )


@app.route("/d/<code>")
def device(code):
    conn = get_db()
    d = device_or_404(code)
    tests = conn.execute(
        "SELECT f.id, f.name, COALESCE(dt.intake, 'untested') AS intake,"
        " COALESCE(dt.current, 'untested') AS current"
        " FROM functions f LEFT JOIN device_tests dt ON dt.function_id = f.id AND dt.device_id = ?"
        " WHERE f.device_type_id = ? ORDER BY f.sort, f.id",
        (d["id"], d["device_type_id"]),
    ).fetchall()
    parts = conn.execute(
        "SELECT * FROM parts WHERE device_type_id = ? ORDER BY sort, name", (d["device_type_id"],)
    ).fetchall()
    # Parts that address something currently failing float to the top of the picker.
    suggested = {r["part_id"] for r in conn.execute(
        "SELECT DISTINCT pf.part_id FROM part_functions pf JOIN device_tests dt"
        " ON dt.function_id = pf.function_id WHERE dt.device_id = ? AND dt.current = 'failed'", (d["id"],))}
    repairs = conn.execute(
        "SELECT r.*, p.name AS part_name, p.part_number, dd.code AS donor_code"
        " FROM repair_items r LEFT JOIN parts p ON p.id = r.part_id"
        " LEFT JOIN devices dd ON dd.id = r.donor_device_id WHERE r.device_id = ? ORDER BY r.id",
        (d["id"],),
    ).fetchall()
    sessions = conn.execute(
        "SELECT * FROM work_sessions WHERE device_id = ? ORDER BY started_at DESC", (d["id"],)
    ).fetchall()
    active = next((s for s in sessions if s["ended_at"] is None), None)
    donor_parts = conn.execute(
        "SELECT dp.*, p.name, p.part_number, p.purpose, u.code AS used_on_code"
        " FROM device_parts dp JOIN parts p ON p.id = dp.part_id"
        " LEFT JOIN devices u ON u.id = dp.used_on_device_id"
        " WHERE dp.device_id = ? ORDER BY p.sort, p.name", (d["id"],)
    ).fetchall() if d["status"] == "parts" else []
    # Donors that could supply this device: same type, or another type with same-named parts.
    donors = conn.execute(
        "SELECT dd.code, t.name AS type_name, p.name AS part_name, dp.state"
        " FROM device_parts dp JOIN devices dd ON dd.id = dp.device_id"
        " JOIN parts p ON p.id = dp.part_id JOIN device_types t ON t.id = dd.device_type_id"
        " WHERE dd.status = 'parts' AND dd.id != ? AND dp.state IN ('good', 'unknown')"
        " AND p.name IN (SELECT name FROM parts WHERE device_type_id = ?)"
        " ORDER BY dd.code, p.sort", (d["id"], d["device_type_id"])
    ).fetchall()
    donor_map = {}
    for row in donors:
        donor_map.setdefault((row["code"], row["type_name"]), []).append(row)
    last_box = None
    last_box_code = request.cookies.get("last_box")
    if last_box_code:
        last_box = conn.execute("SELECT * FROM boxes WHERE code = ?", (last_box_code,)).fetchone()
    return render_template(
        "device.html", d=d, tests=tests, parts=parts, suggested=suggested, repairs=repairs,
        sessions=sessions, active=active, fin=financials(d), donor_parts=donor_parts,
        donor_map=donor_map, last_box=last_box,
        suggested_names={p["name"] for p in parts if p["id"] in suggested},
        long_seconds=setting_float("long_session_hours", 3) * 3600,
        photos=conn.execute("SELECT * FROM photos WHERE device_id = ? ORDER BY id", (d["id"],)).fetchall(),
        boxes=conn.execute("SELECT * FROM boxes ORDER BY code").fetchall(),
        lots=conn.execute("SELECT * FROM lots ORDER BY id DESC").fetchall(),
        failed_count=sum(1 for t in tests if t["current"] == "failed"),
        untested_count=sum(1 for t in tests if t["current"] == "untested"),
    )


@app.route("/d/<code>/update", methods=["POST"])
def device_update(code):
    d = device_or_404(code)
    lot_id = fint("lot_id")
    get_db().execute(
        "UPDATE devices SET serial = ?, notes = ?, box_id = ?, lot_id = ?, manual_cost = ?, updated_at = ?"
        " WHERE id = ?",
        ((request.form.get("serial") or "").strip(), (request.form.get("notes") or "").strip(),
         fint("box_id"), lot_id, fnum("manual_cost"), now(), d["id"]),
    )
    get_db().commit()
    flash("Details saved.", "ok")
    return redirect(url_for("device", code=d["code"]) + "#details")


@app.route("/d/<code>/move", methods=["POST"])
def device_move(code):
    d = device_or_404(code)
    get_db().execute("UPDATE devices SET box_id = ?, updated_at = ? WHERE id = ?", (fint("box_id"), now(), d["id"]))
    get_db().commit()
    flash("Box updated.", "ok")
    return redirect(url_for("device", code=d["code"]))


@app.route("/d/<code>/tests", methods=["POST"])
def device_tests(code):
    """Save checklist results. Before intake is completed this also sets the intake record."""
    conn = get_db()
    d = device_or_404(code)
    valid = {r["id"] for r in conn.execute(
        "SELECT id FROM functions WHERE device_type_id = ?", (d["device_type_id"],))}
    for key, value in request.form.items():
        if not key.startswith("f_") or value not in TEST_RESULTS:
            continue
        try:
            fid = int(key[2:])
        except ValueError:
            continue
        if fid not in valid:
            continue
        if d["intake_done"]:
            conn.execute(
                "INSERT INTO device_tests (device_id, function_id, intake, current) VALUES (?, ?, 'untested', ?)"
                " ON CONFLICT(device_id, function_id) DO UPDATE SET current = excluded.current",
                (d["id"], fid, value),
            )
        else:
            conn.execute(
                "INSERT INTO device_tests (device_id, function_id, intake, current) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(device_id, function_id) DO UPDATE SET intake = excluded.intake,"
                " current = excluded.current",
                (d["id"], fid, value, value),
            )
    touch(d["id"])
    conn.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        counts = conn.execute(
            "SELECT SUM(current = 'failed') AS failed, SUM(current = 'working') AS working"
            " FROM device_tests WHERE device_id = ?", (d["id"],)).fetchone()
        return jsonify(ok=True, failed=counts["failed"] or 0, working=counts["working"] or 0,
                       total=len(valid))
    flash("Checklist saved.", "ok")
    return redirect(url_for("device", code=d["code"]) + "#checklist")


@app.route("/d/<code>/tests/all-working", methods=["POST"])
def device_tests_all(code):
    """Mark every still-untested item as working, to speed up a mostly-fine device."""
    conn = get_db()
    d = device_or_404(code)
    for f in conn.execute("SELECT id FROM functions WHERE device_type_id = ?", (d["device_type_id"],)).fetchall():
        row = conn.execute(
            "SELECT current FROM device_tests WHERE device_id = ? AND function_id = ?", (d["id"], f["id"])
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO device_tests (device_id, function_id, intake, current) VALUES (?, ?, ?, 'working')",
                (d["id"], f["id"], "untested" if d["intake_done"] else "working"),
            )
        elif row["current"] == "untested":
            if d["intake_done"]:
                conn.execute(
                    "UPDATE device_tests SET current = 'working' WHERE device_id = ? AND function_id = ?",
                    (d["id"], f["id"]))
            else:
                conn.execute(
                    "UPDATE device_tests SET current = 'working', intake = 'working'"
                    " WHERE device_id = ? AND function_id = ?", (d["id"], f["id"]))
    touch(d["id"])
    conn.commit()
    return redirect(url_for("device", code=d["code"]) + "#checklist")


@app.route("/d/<code>/intake-done", methods=["POST"])
def device_intake_done(code):
    """Freeze the intake record and move the device to the right status."""
    conn = get_db()
    d = device_or_404(code)
    conn.execute("UPDATE devices SET intake_done = 1, updated_at = ? WHERE id = ?", (now(), d["id"]))
    total = conn.execute(
        "SELECT COUNT(*) FROM functions WHERE device_type_id = ?", (d["device_type_id"],)).fetchone()[0]
    row = conn.execute(
        "SELECT SUM(current = 'failed') AS failed, SUM(current = 'working') AS working"
        " FROM device_tests WHERE device_id = ?", (d["id"],)).fetchone()
    failed, working = row["failed"] or 0, row["working"] or 0
    if d["status"] == "untested":
        if failed:
            set_status(d, "needs_repair")
            flash("Intake saved: %d item(s) failed, so this is now in the repair queue." % failed, "ok")
        elif total and working == total:
            set_status(d, "ready")
            flash("Intake saved: everything works, so this is marked ready. Print an R label.", "ok")
        else:
            flash("Intake saved. Some items are still untested.", "ok")
    conn.commit()
    return redirect(url_for("device", code=d["code"]))


@app.route("/d/<code>/intake-reopen", methods=["POST"])
def device_intake_reopen(code):
    d = device_or_404(code)
    get_db().execute("UPDATE devices SET intake_done = 0, updated_at = ? WHERE id = ?", (now(), d["id"]))
    get_db().commit()
    flash("Intake reopened. Changes now update the intake record again.", "ok")
    return redirect(url_for("device", code=d["code"]) + "#checklist")


@app.route("/d/<code>/status", methods=["POST"])
def device_status(code):
    d = device_or_404(code)
    status = request.form.get("status") or ""
    old_letter = STATUSES[d["status"]][1]
    set_status(d, status)
    get_db().commit()
    if STATUSES[status][1] != old_letter and status != "sold":
        flash("Status is now %s. The label letter changed to %s, so print a new label."
              % (STATUSES[status][0], STATUSES[status][1]), "ok")
    return redirect(url_for("device", code=d["code"]))


@app.route("/d/<code>/timer", methods=["POST"])
def device_timer(code):
    d = device_or_404(code)
    action = request.form.get("action")
    conn = get_db()
    if action == "start":
        other = running_session()
        stop_timers()  # only one timer runs at a time
        conn.execute("INSERT INTO work_sessions (device_id, started_at) VALUES (?, ?)", (d["id"], now()))
        if other and other["device_id"] != d["id"]:
            flash("Paused the timer on %s." % other["device_code"], "ok")
    elif action == "pause":
        stop_timers(d["id"])
    elif action == "add":
        minutes = fnum("minutes")
        if minutes and minutes > 0:
            end = now()
            conn.execute(
                "INSERT INTO work_sessions (device_id, started_at, ended_at) VALUES (?, ?, ?)",
                (d["id"], end - int(minutes * 60), end))
    touch(d["id"])
    conn.commit()
    return back("device", code=d["code"])


@app.route("/session/<int:session_id>/edit", methods=["POST"])
def session_edit(session_id):
    conn = get_db()
    s = conn.execute(
        "SELECT s.*, d.code FROM work_sessions s JOIN devices d ON d.id = s.device_id WHERE s.id = ?",
        (session_id,)).fetchone()
    if s is None:
        abort(404)
    if request.form.get("delete"):
        conn.execute("DELETE FROM work_sessions WHERE id = ?", (session_id,))
    else:
        minutes = fnum("minutes")
        if minutes is not None and minutes >= 0:
            conn.execute("UPDATE work_sessions SET ended_at = ? WHERE id = ?",
                         (s["started_at"] + int(minutes * 60), session_id))
    conn.commit()
    return redirect(url_for("device", code=s["code"]) + "#time")


@app.route("/d/<code>/repair", methods=["POST"])
def repair_add(code):
    conn = get_db()
    d = device_or_404(code)
    source = request.form.get("source") or "purchased"
    if source not in SOURCES:
        abort(400)
    part_id = fint("part_id")
    part = conn.execute(
        "SELECT * FROM parts WHERE id = ? AND device_type_id = ?", (part_id, d["device_type_id"])
    ).fetchone() if part_id else None
    description = (request.form.get("description") or "").strip()
    if part is None and not description:
        flash("Pick a part, or describe what you did.", "error")
        return redirect(url_for("device", code=d["code"]) + "#repair")
    cost = fnum("cost", 0.0) or 0.0

    if source == "donor":
        donor_code = (request.form.get("donor_code") or "").strip().upper()
        donor = conn.execute(DEVICE_SELECT + " WHERE d.code = ?", (donor_code,)).fetchone()
        if donor is None or donor["id"] == d["id"]:
            flash("Donor %s was not found." % (donor_code or "(blank)"), "error")
            return redirect(url_for("device", code=d["code"]) + "#repair")
        if part is None:
            flash("Pick the part you took from the donor.", "error")
            return redirect(url_for("device", code=d["code"]) + "#repair")
        donor_part = conn.execute(
            "SELECT * FROM parts WHERE device_type_id = ? AND name = ?",
            (donor["device_type_id"], part["name"])).fetchone()
        if donor_part is None:
            flash("%s (%s) has no part called \"%s\". Add it to that device type in the Catalog first."
                  % (donor["code"], donor["type_name"], part["name"]), "error")
            return redirect(url_for("device", code=d["code"]) + "#repair")
        taken = conn.execute(
            "SELECT state FROM device_parts WHERE device_id = ? AND part_id = ?",
            (donor["id"], donor_part["id"])).fetchone()
        if taken and taken["state"] == "harvested":
            flash("That part was already taken from %s." % donor["code"], "error")
            return redirect(url_for("device", code=d["code"]) + "#repair")
        if donor["status"] != "parts":
            set_status(donor, "parts")
        record_harvest(donor, donor_part, d, cost, description)
    else:
        conn.execute(
            "INSERT INTO repair_items (device_id, part_id, description, source, cost, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (d["id"], part["id"] if part else None, description, source,
             0.0 if source == "in_place" and fnum("cost") is None else cost, now()))
        touch(d["id"])
    conn.commit()
    flash("Repair step logged. Retest the affected items in the checklist.", "ok")
    return redirect(url_for("device", code=d["code"]) + "#repair")


@app.route("/repair/<int:item_id>/delete", methods=["POST"])
def repair_delete(item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT r.*, d.code FROM repair_items r JOIN devices d ON d.id = r.device_id WHERE r.id = ?",
        (item_id,)).fetchone()
    if item is None:
        abort(404)
    if item["source"] == "donor" and item["donor_device_id"] and item["donor_part_id"]:
        # Undo the harvest so the donor shows the part as available again.
        conn.execute(
            "UPDATE device_parts SET state = 'good', used_on_device_id = NULL"
            " WHERE device_id = ? AND part_id = ? AND state = 'harvested'",
            (item["donor_device_id"], item["donor_part_id"]))
    conn.execute("DELETE FROM repair_items WHERE id = ?", (item_id,))
    conn.commit()
    return redirect(url_for("device", code=item["code"]) + "#repair")


@app.route("/d/<code>/donor-parts", methods=["POST"])
def donor_parts_update(code):
    conn = get_db()
    d = device_or_404(code)
    if request.form.get("rebuild"):
        build_donor_parts(d, overwrite=True)
        flash("Part states rebuilt from the checklist.", "ok")
    else:
        for key, value in request.form.items():
            if key.startswith("state_") and value in PART_STATES and value != "harvested":
                conn.execute(
                    "UPDATE device_parts SET state = ? WHERE device_id = ? AND part_id = ?"
                    " AND state != 'harvested'", (value, d["id"], int(key[6:])))
            elif key.startswith("note_"):
                conn.execute("UPDATE device_parts SET note = ? WHERE device_id = ? AND part_id = ?",
                             (value.strip(), d["id"], int(key[5:])))
        flash("Donor parts saved.", "ok")
    touch(d["id"])
    conn.commit()
    return redirect(url_for("device", code=d["code"]) + "#donor")


@app.route("/d/<code>/harvest", methods=["POST"])
def donor_harvest(code):
    """Harvest from the donor's page: take a part and record which repair it went to."""
    conn = get_db()
    donor = device_or_404(code)
    part = conn.execute(
        "SELECT * FROM parts WHERE id = ? AND device_type_id = ?",
        (fint("part_id"), donor["device_type_id"])).fetchone()
    if part is None:
        abort(400)
    target_code = (request.form.get("target_code") or "").strip().upper()
    target = conn.execute(DEVICE_SELECT + " WHERE d.code = ?", (target_code,)).fetchone()
    if target is None or target["id"] == donor["id"]:
        flash("Device %s was not found." % (target_code or "(blank)"), "error")
        return redirect(url_for("device", code=donor["code"]) + "#donor")
    record_harvest(donor, part, target, fnum("cost", 0.0) or 0.0)
    conn.commit()
    flash("%s taken from %s and logged on %s." % (part["name"], donor["code"], target["code"]), "ok")
    return redirect(url_for("device", code=donor["code"]) + "#donor")


@app.route("/d/<code>/money", methods=["POST"])
def device_money(code):
    d = device_or_404(code)
    get_db().execute("UPDATE devices SET expected_price = ?, updated_at = ? WHERE id = ?",
                     (fnum("expected_price"), now(), d["id"]))
    get_db().commit()
    return redirect(url_for("device", code=d["code"]) + "#money")


@app.route("/d/<code>/sell", methods=["POST"])
def device_sell(code):
    conn = get_db()
    d = device_or_404(code)
    if request.form.get("undo"):
        conn.execute(
            "UPDATE devices SET status = 'ready', sale_price = NULL, sale_fees = NULL, sale_shipping = NULL,"
            " sold_at = NULL, updated_at = ? WHERE id = ?", (now(), d["id"]))
        conn.commit()
        flash("Sale removed. Status is back to Repaired.", "ok")
        return redirect(url_for("device", code=d["code"]) + "#money")
    price = fnum("sale_price")
    if price is None:
        flash("Enter the sale price.", "error")
        return redirect(url_for("device", code=d["code"]) + "#money")
    conn.execute(
        "UPDATE devices SET sale_price = ?, sale_fees = ?, sale_shipping = ?, sold_at = ? WHERE id = ?",
        (price, fnum("sale_fees", 0.0), fnum("sale_shipping", 0.0), now(), d["id"]))
    set_status(d, "sold")
    conn.commit()
    flash("Marked as sold.", "ok")
    return redirect(url_for("device", code=d["code"]) + "#money")


@app.route("/d/<code>/photos", methods=["POST"])
def photo_add(code):
    conn = get_db()
    d = device_or_404(code)
    folder = os.path.join(PHOTO_DIR, d["code"])
    os.makedirs(folder, exist_ok=True)
    saved = 0
    for upload in request.files.getlist("photos"):
        ext = os.path.splitext(upload.filename or "")[1].lower()
        if ext not in PHOTO_EXTENSIONS:
            continue
        name = uuid.uuid4().hex + ext
        upload.save(os.path.join(folder, name))
        conn.execute("INSERT INTO photos (device_id, filename, created_at) VALUES (?, ?, ?)",
                     (d["id"], name, now()))
        saved += 1
    conn.commit()
    flash("%d photo(s) added." % saved if saved else "No image files were uploaded.", "ok" if saved else "error")
    return redirect(url_for("device", code=d["code"]) + "#photos")


@app.route("/photo/<int:photo_id>")
def photo(photo_id):
    row = get_db().execute(
        "SELECT p.filename, d.code FROM photos p JOIN devices d ON d.id = p.device_id WHERE p.id = ?",
        (photo_id,)).fetchone()
    if row is None:
        abort(404)
    path = os.path.join(PHOTO_DIR, row["code"], row["filename"])
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


@app.route("/photo/<int:photo_id>/delete", methods=["POST"])
def photo_delete(photo_id):
    conn = get_db()
    row = conn.execute(
        "SELECT p.filename, d.code FROM photos p JOIN devices d ON d.id = p.device_id WHERE p.id = ?",
        (photo_id,)).fetchone()
    if row is None:
        abort(404)
    path = os.path.join(PHOTO_DIR, row["code"], row["filename"])
    if os.path.exists(path):
        os.remove(path)
    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    conn.commit()
    return redirect(url_for("device", code=row["code"]) + "#photos")


@app.route("/d/<code>/delete", methods=["POST"])
def device_delete(code):
    conn = get_db()
    d = device_or_404(code)
    if request.form.get("confirm") != d["code"]:
        flash("Type the device ID exactly to delete it.", "error")
        return redirect(url_for("device", code=d["code"]) + "#danger")
    # Give harvested parts back to their donors before the repair rows vanish.
    for item in conn.execute(
            "SELECT * FROM repair_items WHERE device_id = ? AND source = 'donor'", (d["id"],)).fetchall():
        if item["donor_device_id"] and item["donor_part_id"]:
            conn.execute(
                "UPDATE device_parts SET state = 'good', used_on_device_id = NULL"
                " WHERE device_id = ? AND part_id = ?", (item["donor_device_id"], item["donor_part_id"]))
    for p in conn.execute("SELECT filename FROM photos WHERE device_id = ?", (d["id"],)).fetchall():
        path = os.path.join(PHOTO_DIR, d["code"], p["filename"])
        if os.path.exists(path):
            os.remove(path)
    conn.execute("DELETE FROM devices WHERE id = ?", (d["id"],))
    conn.commit()
    flash("%s deleted. Its ID will not be reused." % d["code"], "ok")
    return redirect(url_for("devices"))


# ------------------------------------------------------------------- labels

@app.route("/label/d/<code>.png")
def label_device(code):
    d = device_or_404(code)
    png = labels.device_label(
        base_url() + url_for("device", code=d["code"]), d["code"], d["type_name"],
        STATUSES[d["status"]][1], date_filter(d["created_at"]))
    return send_file(png, mimetype="image/png", download_name=d["code"] + ".png")


@app.route("/label/b/<code>.png")
def label_box(code):
    b = get_db().execute("SELECT * FROM boxes WHERE code = ?", (code.upper(),)).fetchone()
    if b is None:
        abort(404)
    png = labels.box_label(base_url() + url_for("box", code=b["code"]), b["code"], b["name"])
    return send_file(png, mimetype="image/png", download_name=b["code"] + ".png")


@app.route("/labels")
def label_sheet():
    conn = get_db()
    device_codes = [c.strip().upper() for c in (request.args.get("d") or "").split(",") if c.strip()]
    box_codes = [c.strip().upper() for c in (request.args.get("b") or "").split(",") if c.strip()]
    found_devices = [r for r in (conn.execute(DEVICE_SELECT + " WHERE d.code = ?", (c,)).fetchone()
                                 for c in device_codes) if r]
    found_boxes = [r for r in (conn.execute("SELECT * FROM boxes WHERE code = ?", (c,)).fetchone()
                               for c in box_codes) if r]
    return render_template("labels.html", devices=found_devices, boxes=found_boxes, base=base_url())


# -------------------------------------------------------------------- boxes

def box_counts(box_id):
    counts = {key: 0 for key in STATUSES}
    for row in get_db().execute(
            "SELECT status, COUNT(*) AS n FROM devices WHERE box_id = ? GROUP BY status", (box_id,)):
        counts[row["status"]] = row["n"]
    return counts


@app.route("/boxes", methods=["GET", "POST"])
def boxes():
    conn = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Give the box a name, like \"DualSense controllers\".", "error")
            return redirect(url_for("boxes"))
        cur = conn.execute("INSERT INTO boxes (name, notes) VALUES (?, ?)",
                           (name, (request.form.get("notes") or "").strip()))
        code = "B-%02d" % cur.lastrowid
        conn.execute("UPDATE boxes SET code = ? WHERE id = ?", (code, cur.lastrowid))
        conn.commit()
        return redirect(url_for("box", code=code))
    rows = conn.execute("SELECT * FROM boxes ORDER BY code").fetchall()
    return render_template("boxes.html", boxes=[(b, box_counts(b["id"])) for b in rows])


@app.route("/b/<code>")
def box(code):
    conn = get_db()
    b = conn.execute("SELECT * FROM boxes WHERE code = ?", (code.strip().upper(),)).fetchone()
    if b is None:
        abort(404)
    devices_in_box = conn.execute(
        DEVICE_SELECT + " WHERE d.box_id = ? ORDER BY CASE d.status WHEN 'untested' THEN 0"
        " WHEN 'needs_repair' THEN 1 WHEN 'ready' THEN 2 WHEN 'parts' THEN 3 ELSE 4 END, d.code",
        (b["id"],)).fetchall()
    # For donors, summarise what is still usable and what has gone where.
    donor_info = {}
    for d in devices_in_box:
        if d["status"] != "parts":
            continue
        rows = conn.execute(
            "SELECT p.name, dp.state, u.code AS used_on FROM device_parts dp JOIN parts p ON p.id = dp.part_id"
            " LEFT JOIN devices u ON u.id = dp.used_on_device_id WHERE dp.device_id = ? ORDER BY p.sort",
            (d["id"],)).fetchall()
        broken = [r["name"] for r in conn.execute(
            "SELECT f.name FROM device_tests dt JOIN functions f ON f.id = dt.function_id"
            " WHERE dt.device_id = ? AND dt.current = 'failed' ORDER BY f.sort", (d["id"],))]
        donor_info[d["id"]] = {
            "good": [r["name"] for r in rows if r["state"] == "good"],
            "taken": [(r["name"], r["used_on"]) for r in rows if r["state"] == "harvested"],
            "broken": broken,
        }
    response = app.make_response(render_template(
        "box.html", b=b, devices=devices_in_box, counts=box_counts(b["id"]), donor_info=donor_info))
    # Remember the last box opened, so a device scanned next can be dropped in with one tap.
    response.set_cookie("last_box", b["code"], max_age=3600 * 12, samesite="Lax")
    return response


@app.route("/b/<code>/add", methods=["POST"])
def box_add(code):
    conn = get_db()
    b = conn.execute("SELECT * FROM boxes WHERE code = ?", (code.upper(),)).fetchone()
    if b is None:
        abort(404)
    added, missing = [], []
    for raw in (request.form.get("codes") or "").replace(",", " ").split():
        device_code = raw.strip().upper()
        if device_code.isdigit():
            device_code = "D-%04d" % int(device_code)
        row = conn.execute("SELECT id FROM devices WHERE code = ?", (device_code,)).fetchone()
        if row:
            conn.execute("UPDATE devices SET box_id = ?, updated_at = ? WHERE id = ?", (b["id"], now(), row["id"]))
            added.append(device_code)
        else:
            missing.append(raw)
    conn.commit()
    if added:
        flash("Added %s." % ", ".join(added), "ok")
    if missing:
        flash("Not found: %s." % ", ".join(missing), "error")
    return redirect(url_for("box", code=b["code"]))


@app.route("/b/<code>/update", methods=["POST"])
def box_update(code):
    conn = get_db()
    b = conn.execute("SELECT * FROM boxes WHERE code = ?", (code.upper(),)).fetchone()
    if b is None:
        abort(404)
    if request.form.get("remove_device"):
        conn.execute("UPDATE devices SET box_id = NULL WHERE code = ? AND box_id = ?",
                     (request.form["remove_device"], b["id"]))
    elif request.form.get("delete"):
        conn.execute("DELETE FROM boxes WHERE id = ?", (b["id"],))
        conn.commit()
        flash("Box %s deleted. Its devices were kept and are now unboxed." % b["code"], "ok")
        return redirect(url_for("boxes"))
    else:
        conn.execute("UPDATE boxes SET name = ?, notes = ? WHERE id = ?",
                     ((request.form.get("name") or b["name"]).strip(),
                      (request.form.get("notes") or "").strip(), b["id"]))
    conn.commit()
    return redirect(url_for("box", code=b["code"]))


# --------------------------------------------------------------------- lots

def lot_summary(lot):
    """Money and time for a whole lot, counting the failures too."""
    conn = get_db()
    target = setting_float("target_rate", 25)
    devices_in_lot = conn.execute(DEVICE_SELECT + " WHERE d.lot_id = ? ORDER BY d.code", (lot["id"],)).fetchall()
    summary = {"count": len(devices_in_lot), "parts": 0.0, "net_sold": 0.0, "net_expected": 0.0,
               "seconds": 0, "sold": 0, "status": {key: 0 for key in STATUSES}, "target": target,
               "cost": float(lot["total_cost"])}
    rows = []
    for d in devices_in_lot:
        fin = financials(d)
        rows.append((d, fin))
        summary["status"][d["status"]] += 1
        summary["parts"] += fin["parts"]
        summary["seconds"] += fin["seconds"]
        if d["status"] == "sold" and fin["net"] is not None:
            summary["net_sold"] += fin["net"]
            summary["sold"] += 1
        elif fin["net"] is not None and d["status"] != "parts":
            summary["net_expected"] += fin["net"]
    hours = summary["seconds"] / 3600.0
    # Realised profit charges the whole lot and every part against what has actually sold.
    summary["profit"] = summary["net_sold"] - summary["cost"] - summary["parts"]
    summary["projected"] = summary["profit"] + summary["net_expected"]
    summary["hourly"] = summary["profit"] / hours if summary["seconds"] >= 60 else None
    summary["hourly_projected"] = summary["projected"] / hours if summary["seconds"] >= 60 else None
    summary["after_labour"] = summary["profit"] - hours * target
    return summary, rows


@app.route("/lots", methods=["GET", "POST"])
def lots():
    conn = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Give the lot a name, like \"eBay 10x DualSense\".", "error")
            return redirect(url_for("lots"))
        purchased = now()
        raw_date = (request.form.get("purchased_at") or "").strip()
        if raw_date:
            try:
                purchased = int(datetime.strptime(raw_date, "%Y-%m-%d").timestamp())
            except ValueError:
                pass
        cur = conn.execute(
            "INSERT INTO lots (name, source, purchased_at, total_cost, unit_count, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (name, (request.form.get("source") or "").strip(), purchased, fnum("total_cost", 0.0) or 0.0,
             max(0, fint("unit_count", 0) or 0), (request.form.get("notes") or "").strip()))
        code = "L-%03d" % cur.lastrowid
        conn.execute("UPDATE lots SET code = ? WHERE id = ?", (code, cur.lastrowid))
        conn.commit()
        return redirect(url_for("lot", code=code))
    rows = conn.execute("SELECT * FROM lots ORDER BY id DESC").fetchall()
    return render_template("lots.html", lots=[(l, lot_summary(l)[0]) for l in rows],
                           today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/l/<code>")
def lot(code):
    conn = get_db()
    l = conn.execute("SELECT * FROM lots WHERE code = ?", (code.strip().upper(),)).fetchone()
    if l is None:
        abort(404)
    summary, rows = lot_summary(l)
    units = max(l["unit_count"] or 0, summary["count"])
    return render_template(
        "lot.html", l=l, s=summary, rows=rows, groups=grouped_types(),
        per_unit=(l["total_cost"] / units) if units else None,
        boxes=conn.execute("SELECT * FROM boxes ORDER BY code").fetchall())


@app.route("/l/<code>/update", methods=["POST"])
def lot_update(code):
    conn = get_db()
    l = conn.execute("SELECT * FROM lots WHERE code = ?", (code.upper(),)).fetchone()
    if l is None:
        abort(404)
    conn.execute(
        "UPDATE lots SET name = ?, source = ?, total_cost = ?, unit_count = ?, notes = ? WHERE id = ?",
        ((request.form.get("name") or l["name"]).strip(), (request.form.get("source") or "").strip(),
         fnum("total_cost", l["total_cost"]), max(0, fint("unit_count", l["unit_count"]) or 0),
         (request.form.get("notes") or "").strip(), l["id"]))
    conn.commit()
    flash("Lot saved.", "ok")
    return redirect(url_for("lot", code=l["code"]))


# ------------------------------------------------------------------ catalog

@app.route("/catalog", methods=["GET", "POST"])
def catalog_index():
    conn = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name the device type.", "error")
        elif conn.execute("SELECT 1 FROM device_types WHERE name = ?", (name,)).fetchone():
            flash("That device type already exists.", "error")
        else:
            copy_from = fint("copy_from")
            functions, parts = [], []
            if copy_from:
                functions = [r["name"] for r in conn.execute(
                    "SELECT name FROM functions WHERE device_type_id = ? ORDER BY sort", (copy_from,))]
                for p in conn.execute("SELECT * FROM parts WHERE device_type_id = ? ORDER BY sort",
                                      (copy_from,)).fetchall():
                    fixes = [r["name"] for r in conn.execute(
                        "SELECT f.name FROM part_functions pf JOIN functions f ON f.id = pf.function_id"
                        " WHERE pf.part_id = ?", (p["id"],))]
                    parts.append((p["name"], p["part_number"], p["purpose"], p["default_cost"],
                                  p["microsolder"], fixes))
            type_id = dbm.add_device_type(
                conn, name, (request.form.get("category") or "Other").strip() or "Other", functions, parts,
                sort=1000)
            conn.commit()
            return redirect(url_for("catalog_type", type_id=type_id))
        return redirect(url_for("catalog_index"))
    q = (request.args.get("q") or "").strip()
    types = conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM parts WHERE device_type_id = t.id) AS part_count,"
        " (SELECT COUNT(*) FROM functions WHERE device_type_id = t.id) AS function_count,"
        " (SELECT COUNT(*) FROM devices WHERE device_type_id = t.id) AS device_count"
        " FROM device_types t ORDER BY t.sort, t.name").fetchall()
    return render_template("catalog.html", types=types, q=q, results=find_parts(q) if q else [])


@app.route("/catalog/<int:type_id>", methods=["GET", "POST"])
def catalog_type(type_id):
    conn = get_db()
    t = conn.execute("SELECT * FROM device_types WHERE id = ?", (type_id,)).fetchone()
    if t is None:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_function":
            name = (request.form.get("name") or "").strip()
            if name:
                top = conn.execute("SELECT COALESCE(MAX(sort), 0) FROM functions WHERE device_type_id = ?",
                                   (type_id,)).fetchone()[0]
                conn.execute("INSERT INTO functions (device_type_id, name, sort) VALUES (?, ?, ?)",
                             (type_id, name, top + 1))
        elif action == "add_part":
            name = (request.form.get("name") or "").strip()
            if name:
                top = conn.execute("SELECT COALESCE(MAX(sort), 0) FROM parts WHERE device_type_id = ?",
                                   (type_id,)).fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO parts (device_type_id, name, part_number, purpose, default_cost, microsolder, sort)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (type_id, name, (request.form.get("part_number") or "").strip(),
                     (request.form.get("purpose") or "").strip(), fnum("default_cost", 0.0) or 0.0,
                     1 if request.form.get("microsolder") else 0, top + 1))
                for fid in request.form.getlist("fixes"):
                    conn.execute("INSERT OR IGNORE INTO part_functions (part_id, function_id) VALUES (?, ?)",
                                 (cur.lastrowid, int(fid)))
        elif action == "save_costs":
            for key, value in request.form.items():
                if key.startswith("cost_"):
                    try:
                        conn.execute("UPDATE parts SET default_cost = ? WHERE id = ? AND device_type_id = ?",
                                     (float(value.replace(",", ".") or 0), int(key[5:]), type_id))
                    except ValueError:
                        pass
            flash("Costs saved.", "ok")
        conn.commit()
        return redirect(url_for("catalog_type", type_id=type_id))
    functions = conn.execute("SELECT * FROM functions WHERE device_type_id = ? ORDER BY sort, id",
                             (type_id,)).fetchall()
    parts = []
    for p in conn.execute("SELECT * FROM parts WHERE device_type_id = ? ORDER BY sort, id", (type_id,)).fetchall():
        fixes = [r["name"] for r in conn.execute(
            "SELECT f.name FROM part_functions pf JOIN functions f ON f.id = pf.function_id"
            " WHERE pf.part_id = ? ORDER BY f.sort", (p["id"],))]
        parts.append((p, fixes))
    return render_template("catalog_type.html", t=t, functions=functions, parts=parts)


# ------------------------------------------------------------------ reports

@app.route("/reports")
def reports():
    conn = get_db()
    target = setting_float("target_rate", 25)
    lot_rows = [(l, lot_summary(l)[0]) for l in conn.execute("SELECT * FROM lots ORDER BY id DESC").fetchall()]

    # Devices bought one at a time, outside any lot.
    loose = {"count": 0, "cost": 0.0, "parts": 0.0, "net_sold": 0.0, "seconds": 0, "sold": 0}
    for d in conn.execute(DEVICE_SELECT + " WHERE d.lot_id IS NULL").fetchall():
        fin = financials(d)
        loose["count"] += 1
        loose["cost"] += fin["basis"]
        loose["parts"] += fin["parts"]
        loose["seconds"] += fin["seconds"]
        if d["status"] == "sold" and fin["net"] is not None:
            loose["net_sold"] += fin["net"]
            loose["sold"] += 1
    loose["profit"] = loose["net_sold"] - loose["cost"] - loose["parts"]
    loose["hourly"] = loose["profit"] / (loose["seconds"] / 3600.0) if loose["seconds"] >= 60 else None

    total_seconds = sum(s["seconds"] for _, s in lot_rows) + loose["seconds"]
    total_profit = sum(s["profit"] for _, s in lot_rows) + loose["profit"]
    overall = {
        "seconds": total_seconds, "profit": total_profit,
        "hourly": total_profit / (total_seconds / 3600.0) if total_seconds >= 60 else None,
        "after_labour": total_profit - (total_seconds / 3600.0) * target,
    }

    # Average bench time by repair type: devices that came out working, grouped by what was replaced.
    groups = {}
    for d in conn.execute(DEVICE_SELECT + " WHERE d.status IN ('ready', 'sold')").fetchall():
        items = conn.execute(
            "SELECT COALESCE(p.name, r.description) AS label, r.cost FROM repair_items r"
            " LEFT JOIN parts p ON p.id = r.part_id WHERE r.device_id = ?", (d["id"],)).fetchall()
        seconds = worked_seconds(d["id"])
        if not items or seconds < 60:
            continue
        key = (d["type_name"], " + ".join(sorted({i["label"] or "Unnamed step" for i in items})))
        entry = groups.setdefault(key, {"n": 0, "seconds": 0, "parts": 0.0})
        entry["n"] += 1
        entry["seconds"] += seconds
        entry["parts"] += sum(i["cost"] for i in items)
    repair_types = sorted(
        ({"type": k[0], "repair": k[1], "n": v["n"], "avg_seconds": v["seconds"] / v["n"],
          "avg_parts": v["parts"] / v["n"], "labour": (v["seconds"] / v["n"] / 3600.0) * target}
         for k, v in groups.items()),
        key=lambda r: (r["type"], -r["n"]))

    # Time spent on devices that ended up as donors.
    failures = []
    for row in conn.execute(
            "SELECT t.name AS type_name, d.id FROM devices d JOIN device_types t ON t.id = d.device_type_id"
            " WHERE d.status = 'parts'").fetchall():
        failures.append((row["type_name"], worked_seconds(row["id"])))
    failure_summary = {}
    for name, seconds in failures:
        entry = failure_summary.setdefault(name, {"n": 0, "seconds": 0})
        entry["n"] += 1
        entry["seconds"] += seconds
    return render_template("reports.html", lot_rows=lot_rows, loose=loose, overall=overall, target=target,
                           repair_types=repair_types, failure_summary=failure_summary)


# ----------------------------------------------------------------- settings

@app.route("/settings", methods=["GET", "POST"])
def settings():
    conn = get_db()
    if request.method == "POST":
        for key in dbm.DEFAULT_SETTINGS:
            if key in request.form:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, request.form[key].strip()))
        conn.commit()
        if "ebay_client_id" in request.form:
            keys = system.get_secret("ebay")
            keys["client_id"] = request.form["ebay_client_id"].strip()
            # A blank secret field means "keep the one already saved".
            if request.form.get("ebay_client_secret", "").strip():
                keys["client_secret"] = request.form["ebay_client_secret"].strip()
            if not keys["client_id"]:
                keys = {}
            system.set_secret("ebay", keys)
        flash("Settings saved.", "ok")
        return redirect(url_for("settings"))
    values = {key: setting(key, default) for key, default in dbm.DEFAULT_SETTINGS.items()}
    ebay = system.get_secret("ebay")
    return render_template("settings.html", values=values, detected=request.url_root.rstrip("/"),
                           ebay_client_id=ebay.get("client_id", ""), ebay_has_secret=bool(ebay.get("client_secret")),
                           marketplaces=pricing.MARKETPLACES)


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=%s" % filename})


@app.route("/export/<name>.csv")
def export(name):
    conn = get_db()
    if name == "devices":
        rows = []
        for d in conn.execute(DEVICE_SELECT + " ORDER BY d.id").fetchall():
            fin = financials(d)
            rows.append([
                d["code"], d["type_name"], STATUSES[d["status"]][0], d["serial"], d["lot_code"] or "",
                d["box_code"] or "", date_filter(d["created_at"]), date_filter(d["sold_at"]),
                "%.2f" % fin["basis"], "%.2f" % fin["parts"],
                "" if d["sale_price"] is None else "%.2f" % d["sale_price"],
                "" if d["sale_fees"] is None else "%.2f" % d["sale_fees"],
                "" if d["sale_shipping"] is None else "%.2f" % d["sale_shipping"],
                "" if fin["profit"] is None or fin["estimate"] else "%.2f" % fin["profit"],
                "%.1f" % (fin["seconds"] / 60.0), d["notes"]])
        return csv_response("devices.csv", [
            "id", "type", "status", "serial", "lot", "box", "received", "sold", "cost_basis", "parts_cost",
            "sale_price", "sale_fees", "sale_shipping", "profit", "minutes_worked", "notes"], rows)
    if name == "repairs":
        rows = conn.execute(
            "SELECT d.code, t.name, COALESCE(p.name, ''), COALESCE(p.part_number, ''), r.description, r.source,"
            " COALESCE(dd.code, ''), r.cost, r.created_at FROM repair_items r"
            " JOIN devices d ON d.id = r.device_id JOIN device_types t ON t.id = d.device_type_id"
            " LEFT JOIN parts p ON p.id = r.part_id LEFT JOIN devices dd ON dd.id = r.donor_device_id"
            " ORDER BY r.id").fetchall()
        return csv_response("repairs.csv", [
            "device", "type", "part", "part_number", "note", "source", "donor", "cost", "date"],
            [list(r[:8]) + [datetime_filter(r[8])] for r in rows])
    if name == "sessions":
        rows = conn.execute(
            "SELECT d.code, s.started_at, s.ended_at FROM work_sessions s JOIN devices d ON d.id = s.device_id"
            " ORDER BY s.started_at").fetchall()
        return csv_response("sessions.csv", ["device", "started", "ended", "minutes"], [
            [r[0], datetime_filter(r[1]), datetime_filter(r[2]),
             "" if r[2] is None else "%.1f" % ((r[2] - r[1]) / 60.0)] for r in rows])
    abort(404)


@app.route("/backup")
def backup():
    """Download a consistent copy of the database, safe to take while the app is running."""
    if not admin_unlocked():
        flash("Unlock system actions to download backups.", "error")
        return redirect(url_for("system_page") + "#admin")
    path = os.path.join(DATA_DIR, "backup-download.db")
    if os.path.exists(path):
        os.remove(path)
    target = sqlite3.connect(path)
    with target:
        get_db().backup(target)
    target.close()
    return send_file(path, as_attachment=True,
                     download_name="benchlog-%s.db" % datetime.now().strftime("%Y%m%d-%H%M"))


# -------------------------------------------------------------------- parts

def part_or_404(part_id):
    row = get_db().execute(
        "SELECT p.*, t.name AS type_name FROM parts p JOIN device_types t ON t.id = p.device_type_id"
        " WHERE p.id = ?", (part_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def render_part(part, **extra):
    conn = get_db()
    fixes = [r["name"] for r in conn.execute(
        "SELECT f.name FROM part_functions pf JOIN functions f ON f.id = pf.function_id"
        " WHERE pf.part_id = ? ORDER BY f.sort", (part["id"],))]
    donors = conn.execute(
        "SELECT d.code, dp.state FROM device_parts dp JOIN devices d ON d.id = dp.device_id"
        " WHERE dp.part_id = ? AND d.status = 'parts' AND dp.state IN ('good', 'unknown')"
        " ORDER BY dp.state, d.code", (part["id"],)).fetchall()
    used = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(AVG(cost), 0) AS avg FROM repair_items"
        " WHERE part_id = ? AND source = 'purchased'", (part["id"],)).fetchone()
    query = extra.pop("query", None) or part["last_price_query"] or pricing.part_query(
        part["name"], part["part_number"], part["type_name"])
    keys = system.get_secret("ebay")
    return render_template(
        "part.html", p=part, fixes=fixes, donors=donors, used=used, query=query,
        ebay_ready=bool(keys.get("client_id") and keys.get("client_secret")),
        search_links=pricing.links(part["name"], part["part_number"], part["type_name"],
                                   setting("ebay_marketplace", "EBAY_US"), query=query),
        **extra)


@app.route("/part/<int:part_id>", methods=["GET", "POST"])
def part(part_id):
    conn = get_db()
    p = part_or_404(part_id)
    if request.method == "POST":
        if "use_price" in request.form:
            price = fnum("use_price")
            if price is not None and price >= 0:
                conn.execute("UPDATE parts SET default_cost = ? WHERE id = ?", (round(price, 2), part_id))
                flash("Usual cost set to %s." % money_filter(round(price, 2)), "ok")
        else:
            name = (request.form.get("name") or "").strip() or p["name"]
            conn.execute(
                "UPDATE parts SET name = ?, part_number = ?, purpose = ?, default_cost = ?, buy_url = ?,"
                " microsolder = ? WHERE id = ?",
                (name, (request.form.get("part_number") or "").strip(),
                 (request.form.get("purpose") or "").strip(), fnum("default_cost", p["default_cost"]) or 0.0,
                 (request.form.get("buy_url") or "").strip(), 1 if request.form.get("microsolder") else 0,
                 part_id))
            flash("Part saved.", "ok")
        conn.commit()
        return redirect(url_for("part", part_id=part_id))
    return render_part(p)


@app.route("/part/<int:part_id>/price", methods=["POST"])
def part_price(part_id):
    """Look the part up on eBay and show what it is selling for. Nothing is saved until confirmed."""
    conn = get_db()
    p = part_or_404(part_id)
    query = (request.form.get("query") or "").strip() or pricing.part_query(
        p["name"], p["part_number"], p["type_name"])
    keys = system.get_secret("ebay")
    try:
        items = pricing.search(query, keys.get("client_id", ""), keys.get("client_secret", ""),
                               setting("ebay_marketplace", "EBAY_US"),
                               new_only=not request.form.get("include_used"))
    except pricing.PriceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("part", part_id=part_id))
    summary = pricing.summarize(items)
    if summary:
        conn.execute("UPDATE parts SET last_price = ?, last_price_at = ?, last_price_query = ? WHERE id = ?",
                     (round(summary["median"], 2), now(), query, part_id))
        conn.commit()
        p = part_or_404(part_id)
    else:
        flash("eBay returned no listings for that search. Try different words.", "error")
    return render_part(p, items=items, summary=summary, query=query)


# ------------------------------------------------------------------- system

ADMIN_MINUTES = 15


def admin_unlocked():
    return system.admin_is_set() and session.get("admin_until", 0) > now()


def admin_guard():
    """Return a redirect when system actions are locked, otherwise None."""
    if admin_unlocked():
        session["admin_until"] = now() + ADMIN_MINUTES * 60
        return None
    flash("Unlock system actions with the admin password first.", "error")
    return redirect(url_for("system_page") + "#admin")


def sudo_password():
    """Return (password or None, ok). No password is needed when sudo never asks for one."""
    if system.sudo_passwordless():
        return None, True
    typed = request.form.get("sudo_password") or ""
    if not typed:
        flash("Enter the password of the account the app runs under. It is used once and not stored.", "error")
        return None, False
    if not system.verify_sudo(typed):
        flash("That account password was not accepted.", "error")
        return None, False
    return typed, True


@app.route("/system")
def system_page():
    return render_template(
        "system.html", h=system.read_health(), version=system.app_version(),
        app_update=system.load_json("app-update.json"), os_updates=system.load_json("os-updates.json"),
        update_state=system.load_json("update-state.json"), backups=system.list_backups(),
        job=system.running_job(), admin_set=system.admin_is_set(), unlocked=admin_unlocked(),
        sudo_free=system.sudo_passwordless(), has_apt=system.has_apt())


@app.route("/system/health")
def system_health():
    return render_template("_health.html", h=system.read_health())


@app.route("/system/ping")
def system_ping():
    return jsonify(ok=True, started=int(system.STARTED_AT))


@app.route("/system/admin", methods=["POST"])
def system_admin():
    action = request.form.get("action")
    password = request.form.get("admin_password") or ""
    if action == "set":
        if system.admin_is_set() and not system.check_admin(request.form.get("current_password") or ""):
            time.sleep(1)
            flash("The current admin password is wrong.", "error")
        elif len(password) < 8:
            flash("Use at least 8 characters for the admin password.", "error")
        elif password != (request.form.get("confirm_password") or ""):
            flash("The two passwords do not match.", "error")
        else:
            system.set_admin(password)
            session["admin_until"] = now() + ADMIN_MINUTES * 60
            flash("Admin password saved. System actions are unlocked for %d minutes." % ADMIN_MINUTES, "ok")
    elif action == "unlock":
        if system.check_admin(password):
            session["admin_until"] = now() + ADMIN_MINUTES * 60
            flash("Unlocked for %d minutes." % ADMIN_MINUTES, "ok")
        else:
            time.sleep(1)  # slows down guessing
            flash("Wrong admin password.", "error")
    elif action == "lock":
        session.pop("admin_until", None)
    return redirect(url_for("system_page") + "#admin")


def launch(kind, title, steps, **kwargs):
    job = system.start_job(kind, title, steps, **kwargs)
    if job is None:
        flash("Another system task is still running. Wait for it to finish.", "error")
        return redirect(url_for("system_page"))
    return redirect(url_for("system_job", job_id=job["id"]))


@app.route("/system/reboot", methods=["POST"])
def system_reboot():
    blocked = admin_guard()
    if blocked:
        return blocked
    password, ok = sudo_password()
    if not ok:
        return redirect(url_for("system_page") + "#power")
    if system.running_job() is not None:
        flash("A system task is still running. Reboot after it finishes.", "error")
        return redirect(url_for("system_page") + "#power")
    stop_timers()
    get_db().commit()
    try:
        system.create_backup("auto")
    except Exception:
        pass
    system.reboot(password)
    return render_template("reboot.html", started=int(system.STARTED_AT))


@app.route("/system/restart", methods=["POST"])
def system_restart():
    blocked = admin_guard()
    if blocked:
        return blocked
    system.restart_app()
    return render_template("reboot.html", started=int(system.STARTED_AT), app_only=True)


@app.route("/system/os/<action>", methods=["POST"])
def system_os(action):
    blocked = admin_guard()
    if blocked:
        return blocked
    if not system.has_apt():
        flash("This system does not use apt, so updates have to be done by hand.", "error")
        return redirect(url_for("system_page") + "#os")
    password, ok = sudo_password()
    if not ok:
        return redirect(url_for("system_page") + "#os")
    if action == "check":
        return launch("os-check", "Check for system updates", system.os_check_steps(password))
    if action == "apply":
        if not system.load_json("os-updates.json").get("packages"):
            flash("Check for updates first, so you can see what will change.", "error")
            return redirect(url_for("system_page") + "#os")
        return launch("os-upgrade", "Install system updates", system.os_upgrade_steps(password))
    abort(404)


@app.route("/system/app/<action>", methods=["POST"])
def system_app(action):
    blocked = admin_guard()
    if blocked:
        return blocked
    if action == "check":
        result = system.check_app_update()
        if result["error"]:
            flash(result["error"], "error")
        elif result["behind"]:
            flash("%d update(s) available. Review them below before applying." % result["behind"], "ok")
        else:
            flash("Bench Log is up to date.", "ok")
        return redirect(url_for("system_page") + "#app")
    version = system.app_version()
    if not version["is_git"]:
        flash("This copy was not installed with git, so it cannot update itself.", "error")
        return redirect(url_for("system_page") + "#app")
    if version["dirty"]:
        flash("Files in the app folder were edited by hand. Undo those edits before updating.", "error")
        return redirect(url_for("system_page") + "#app")
    if action == "apply":
        if not system.load_json("app-update.json").get("behind"):
            flash("Check for updates first.", "error")
            return redirect(url_for("system_page") + "#app")
        stop_timers()
        get_db().commit()
        steps, undo, restart = system.update_steps(system.restart_app)
        return launch("app-update", "Update Bench Log", steps, on_failure=undo,
                      on_success=lambda log: restart(), restarts=True)
    if action == "rollback":
        if not system.load_json("update-state.json").get("previous"):
            flash("There is no earlier version to return to.", "error")
            return redirect(url_for("system_page") + "#app")
        stop_timers()
        get_db().commit()
        steps, restart = system.rollback_steps(system.restart_app)
        return launch("app-rollback", "Return to the previous version", steps,
                      on_success=lambda log: restart(), restarts=True)
    abort(404)


@app.route("/system/job/<job_id>")
def system_job(job_id):
    job = system.get_job(job_id)
    if job is None:
        abort(404)
    return render_template("job.html", job=job, log=system.job_log(job_id), started=int(system.STARTED_AT))


@app.route("/system/job/<job_id>.json")
def system_job_json(job_id):
    job = system.get_job(job_id)
    if job is None:
        abort(404)
    return jsonify(status=job["status"], log=system.job_log(job_id), restarts=job.get("restarts", False))


@app.route("/system/backups", methods=["POST"])
def system_backups():
    blocked = admin_guard()
    if blocked:
        return blocked
    action = request.form.get("action")
    name = request.form.get("name") or ""
    try:
        if action == "create":
            flash("Backup saved as %s." % system.create_backup("manual"), "ok")
        elif action == "restore":
            if request.form.get("confirm") != "RESTORE":
                flash("Type RESTORE to confirm. Restoring replaces everything entered since that backup.", "error")
            else:
                stop_timers()
                get_db().commit()
                saved = system.restore_backup(name)
                flash("Restored %s. The database from just before was kept as %s." % (name, saved), "ok")
        elif action == "delete":
            path = system.backup_path(name)
            if path:
                os.remove(path)
                flash("Deleted %s." % name, "ok")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("system_page") + "#backups")


@app.route("/system/backups/<name>")
def system_backup_download(name):
    blocked = None if admin_unlocked() else redirect(url_for("system_page") + "#admin")
    if blocked:
        flash("Unlock system actions to download backups.", "error")
        return blocked
    path = system.backup_path(name)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name="benchlog-" + name)


@app.route("/system/full-backup.zip")
def system_full_backup():
    if not admin_unlocked():
        flash("Unlock system actions to download backups.", "error")
        return redirect(url_for("system_page") + "#admin")
    return send_file(system.full_archive(), as_attachment=True,
                     download_name="benchlog-full-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M"))


@app.errorhandler(404)
def not_found(_err):
    return render_template("404.html"), 404


if __name__ == "__main__":
    host = os.environ.get("BENCHLOG_HOST", "0.0.0.0")
    port = int(os.environ.get("BENCHLOG_PORT", "8080"))
    import threading
    threading.Thread(target=system.backup_loop, daemon=True).start()
    try:
        from waitress import serve
        print("Bench Log running at http://%s:%d" % (host, port))
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        app.run(host=host, port=port)
