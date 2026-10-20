import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    capacity INTEGER NOT NULL,
    office   TEXT    NOT NULL            -- Berlin | Denver; drives the timezone
);

CREATE TABLE IF NOT EXISTS series (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id      INTEGER NOT NULL REFERENCES rooms(id),
    user         TEXT    NOT NULL,
    first_start  TEXT    NOT NULL,       -- naive local ISO, first requested occurrence
    repeat_until TEXT    NOT NULL,       -- date, inclusive
    created_at   INTEGER NOT NULL,
    cancelled_at INTEGER
);

CREATE TABLE IF NOT EXISTS bookings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id      INTEGER NOT NULL REFERENCES rooms(id),
    user         TEXT    NOT NULL,
    series_id    INTEGER REFERENCES series(id),
    start_utc    INTEGER NOT NULL,       -- epoch seconds; used for overlap + "future"
    end_utc      INTEGER NOT NULL,
    start_local  TEXT    NOT NULL,       -- naive local ISO; returned verbatim (C1)
    end_local    TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    cancelled_at INTEGER,
    CHECK (end_utc > start_utc)
);
CREATE INDEX IF NOT EXISTS idx_bookings_room_time
    ON bookings(room_id, start_utc, end_utc) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_bookings_series ON bookings(series_id);
"""

# Matches the sample GET /rooms response in constraint C2: ids are NOT sequential.
# Office assignment is our assumption (the brief gives rooms no location) - see DECISIONS.md.
SEED_ROOMS = [
    (3, "Aurora", 8, "Berlin"),
    (4, "Basalt", 4, "Berlin"),
    (9, "Cinder", 12, "Denver"),
    (17, "Dune", 6, "Denver"),
]


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves so transactions are explicit.
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
            conn.executemany("INSERT INTO rooms VALUES (?, ?, ?, ?)", SEED_ROOMS)
    finally:
        conn.close()
