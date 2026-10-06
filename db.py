"""SQLite schema, connection helper, and catalog seeding."""

import sqlite3

import catalog

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS device_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'Other',
    sort INTEGER NOT NULL DEFAULT 0
);

/* One row per thing you test on a device type (the intake checklist). */
CREATE TABLE IF NOT EXISTS functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_type_id INTEGER NOT NULL REFERENCES device_types(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_type_id INTEGER NOT NULL REFERENCES device_types(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    part_number TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    default_cost REAL NOT NULL DEFAULT 0,
    microsolder INTEGER NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);

/* Which checklist items a part fixes. This is what makes reverse search work. */
CREATE TABLE IF NOT EXISTS part_functions (
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
    PRIMARY KEY (part_id, function_id)
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    purchased_at INTEGER,
    total_cost REAL NOT NULL DEFAULT 0,
    unit_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    device_type_id INTEGER NOT NULL REFERENCES device_types(id),
    status TEXT NOT NULL DEFAULT 'untested',
    serial TEXT NOT NULL DEFAULT '',
    lot_id INTEGER REFERENCES lots(id) ON DELETE SET NULL,
    manual_cost REAL,
    box_id INTEGER REFERENCES boxes(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '',
    intake_done INTEGER NOT NULL DEFAULT 0,
    expected_price REAL,
    sale_price REAL,
    sale_fees REAL,
    sale_shipping REAL,
    sold_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

/* Checklist results. "intake" is frozen when intake is completed, "current" keeps changing as the device is repaired and retested. */
CREATE TABLE IF NOT EXISTS device_tests (
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
    intake TEXT NOT NULL DEFAULT 'untested',
    current TEXT NOT NULL DEFAULT 'untested',
    PRIMARY KEY (device_id, function_id)
);

/* State of each part on a donor device. */
CREATE TABLE IF NOT EXISTS device_parts (
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'unknown',
    used_on_device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, part_id)
);

/* One row per thing done to a device during repair. */
CREATE TABLE IF NOT EXISTS repair_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    part_id INTEGER REFERENCES parts(id) ON DELETE SET NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'purchased',
    donor_device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    donor_part_id INTEGER REFERENCES parts(id) ON DELETE SET NULL,
    cost REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS work_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_box ON devices(box_id);
CREATE INDEX IF NOT EXISTS idx_devices_lot ON devices(lot_id);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON work_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_repairs_device ON repair_items(device_id);
"""

DEFAULT_SETTINGS = {
    "target_rate": "25",
    "currency": "$",
    "base_url": "",
    "long_session_hours": "3",
    "ebay_marketplace": "EBAY_US",
}

# Columns added after the first release: (table, column, declaration).
# New installs and old databases both end up with the same layout.
MIGRATIONS = [
    ("parts", "buy_url", "TEXT NOT NULL DEFAULT ''"),
    ("parts", "last_price", "REAL"),
    ("parts", "last_price_at", "INTEGER"),
    ("parts", "last_price_query", "TEXT NOT NULL DEFAULT ''"),
]


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def add_device_type(conn, name, category, functions, parts, sort=0):
    """Insert a device type with its checklist and parts. Returns the new id."""
    cur = conn.execute(
        "INSERT INTO device_types (name, category, sort) VALUES (?, ?, ?)", (name, category, sort)
    )
    type_id = cur.lastrowid
    function_ids = {}
    for i, fname in enumerate(functions):
        c = conn.execute(
            "INSERT INTO functions (device_type_id, name, sort) VALUES (?, ?, ?)", (type_id, fname, i)
        )
        function_ids[fname] = c.lastrowid
    for i, (pname, number, purpose, cost, micro, fixes) in enumerate(parts):
        c = conn.execute(
            "INSERT INTO parts (device_type_id, name, part_number, purpose, default_cost, microsolder, sort)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (type_id, pname, number, purpose, cost, 1 if micro else 0, i),
        )
        for fname in fixes:
            if fname in function_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO part_functions (part_id, function_id) VALUES (?, ?)",
                    (c.lastrowid, function_ids[fname]),
                )
    return type_id


def init(conn):
    conn.executescript(SCHEMA)
    for table, column, declaration in MIGRATIONS:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in existing:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, declaration))
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if conn.execute("SELECT COUNT(*) FROM device_types").fetchone()[0] == 0:
        for i, t in enumerate(catalog.CATALOG):
            add_device_type(conn, t["name"], t["category"], t["functions"], t["parts"], sort=i)
    conn.commit()
