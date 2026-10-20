"""Booking domain logic. No HTTP in here."""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import db
from .timeutil import (OFFICE_TZ, UTC, NonexistentLocalTime, format_local,
                       parse_local, to_epoch)

MAX_SKIPPED = 2          # R2: "one or two" clashes are skipped; more than that fails the series
MAX_OCCURRENCES = 53     # a year of weekly bookings; stops accidental 10-year series


class DomainError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class InvalidInput(DomainError):
    status, code = 422, "invalid_input"


class NotFound(DomainError):
    status, code = 404, "not_found"


class Conflict(DomainError):
    status, code = 409, "conflict"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BookingService:
    def __init__(self, db_path: str, now: Optional[Callable[[], datetime]] = None,
                 max_skipped: int = MAX_SKIPPED, max_occurrences: int = MAX_OCCURRENCES):
        self.db_path = db_path
        self._now = now or _utc_now      # injectable so tests can move time
        self.max_skipped = max_skipped
        self.max_occurrences = max_occurrences
        db.init_db(db_path)

    # ---------- plumbing ----------
    def _now_epoch(self) -> int:
        return int(self._now().timestamp())

    @contextmanager
    def _read(self):
        conn = db.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self):
        """Write transaction. BEGIN IMMEDIATE takes the write lock up front, so the
        conflict check and the insert are atomic with respect to other writers."""
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def _room(conn, room_id: int):
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if row is None:
            raise NotFound(f"room {room_id} does not exist")
        return row

    @staticmethod
    def _clean_user(user: str) -> str:
        user = (user or "").strip()
        if not user or len(user) > 100:
            raise InvalidInput("user must be 1-100 characters")
        return user

    @staticmethod
    def _parse_range(start: str, end: str):
        try:
            s, e = parse_local(start, "start"), parse_local(end, "end")
        except ValueError as exc:
            raise InvalidInput(str(exc))
        if e <= s:
            raise InvalidInput("end must be after start")
        return s, e

    @staticmethod
    def _epochs(tz, s: datetime, e: datetime):
        return to_epoch(s, tz), to_epoch(e, tz)

    @staticmethod
    def _find_conflict(conn, room_id: int, s_utc: int, e_utc: int):
        # R4: half-open intervals [start, end). Back-to-back (end == next start) does not match.
        return conn.execute(
            "SELECT id FROM bookings WHERE room_id = ? AND status = 'active' "
            "AND start_utc < ? AND end_utc > ? ORDER BY start_utc LIMIT 1",
            (room_id, e_utc, s_utc)).fetchone()

    @staticmethod
    def _insert(conn, room_id, user, series_id, s_local, e_local, s_utc, e_utc) -> dict:
        cur = conn.execute(
            "INSERT INTO bookings (room_id, user, series_id, start_utc, end_utc, start_local, end_local) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (room_id, user, series_id, s_utc, e_utc, format_local(s_local), format_local(e_local)))
        return {"id": cur.lastrowid, "room_id": room_id, "user": user, "series_id": series_id,
                "start": format_local(s_local), "end": format_local(e_local), "status": "active"}

    @staticmethod
    def _booking_json(row) -> dict:
        return {"id": row["id"], "room_id": row["room_id"], "user": row["user"],
                "series_id": row["series_id"], "start": row["start_local"],
                "end": row["end_local"], "status": row["status"]}

    # ---------- rooms ----------
    def list_rooms(self) -> list:
        # Exactly {id, name, capacity}: the facilities dashboard renders whatever comes back (C2),
        # so internal columns such as `office` must not leak into this response.
        with self._read() as conn:
            rows = conn.execute("SELECT id, name, capacity FROM rooms ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def availability(self, start: str, end: str) -> list:
        """Rooms free for the given wall-clock window. The window is read as local time in each
        room's own office. Iterates the rooms that exist - ids are not 1..N (R5 vs C2)."""
        s, e = self._parse_range(start, end)
        free = []
        with self._read() as conn:
            for room in conn.execute("SELECT * FROM rooms ORDER BY id").fetchall():
                try:
                    s_utc, e_utc = self._epochs(OFFICE_TZ[room["office"]], s, e)
                except NonexistentLocalTime:
                    continue
                if self._find_conflict(conn, room["id"], s_utc, e_utc) is None:
                    free.append({"id": room["id"], "name": room["name"], "capacity": room["capacity"]})
        return free

    # ---------- create ----------
    def create_booking(self, room_id: int, start: str, end: str, user: str) -> dict:
        user = self._clean_user(user)
        s, e = self._parse_range(start, end)
        with self._tx() as conn:
            tz = OFFICE_TZ[self._room(conn, room_id)["office"]]
            try:
                s_utc, e_utc = self._epochs(tz, s, e)
            except NonexistentLocalTime as exc:
                raise InvalidInput(str(exc))
            clash = self._find_conflict(conn, room_id, s_utc, e_utc)
            if clash:
                raise Conflict("room is already booked for that time", conflicts_with=clash["id"])
            return self._insert(conn, room_id, user, None, s, e, s_utc, e_utc)

    def create_recurring(self, room_id: int, start: str, end: str, user: str,
                         repeat: str, repeat_until: str) -> dict:
        user = self._clean_user(user)
        s, e = self._parse_range(start, end)
        if repeat != "weekly":
            raise InvalidInput("only repeat='weekly' is supported")
        try:
            until = date.fromisoformat(repeat_until)
        except (TypeError, ValueError):
            raise InvalidInput("repeat_until must be a date like 2026-12-28")
        if until < s.date():
            raise InvalidInput("repeat_until must not be before the first booking date")
        count = (until - s.date()).days // 7 + 1
        if count > self.max_occurrences:
            raise InvalidInput(f"a series may have at most {self.max_occurrences} occurrences "
                               f"(this one would have {count})")

        duration = e - s  # naive (wall-clock) difference, re-applied to each occurrence
        with self._tx() as conn:
            tz = OFFICE_TZ[self._room(conn, room_id)["office"]]
            series_id = conn.execute(
                "INSERT INTO series (room_id, user, first_start, repeat_until, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (room_id, user, format_local(s), until.isoformat(), self._now_epoch())).lastrowid
            created, skipped = [], []
            for k in range(count):
                # R3: step the *local* date by 7 days and keep the local clock time. Adding
                # 7*24h to a UTC instant is what shifts bookings by an hour across DST.
                occ_s = s + timedelta(days=7 * k)
                occ_e = occ_s + duration
                try:
                    s_utc, e_utc = self._epochs(tz, occ_s, occ_e)
                except NonexistentLocalTime:
                    skipped.append({"start": format_local(occ_s), "end": format_local(occ_e),
                                    "reason": "nonexistent_local_time"})
                    continue
                clash = self._find_conflict(conn, room_id, s_utc, e_utc)
                if clash:
                    skipped.append({"start": format_local(occ_s), "end": format_local(occ_e),
                                    "reason": "conflict", "conflicts_with": clash["id"]})
                    continue
                created.append(self._insert(conn, room_id, user, series_id, occ_s, occ_e, s_utc, e_utc))

            # R1 + R2: skip a clash or two, but never leave a half-made series. Raising here
            # rolls the whole transaction back, including the series row.
            if not created:
                raise Conflict("no occurrence could be booked; nothing was saved", skipped=skipped)
            if len(skipped) > self.max_skipped:
                raise Conflict(f"{len(skipped)} occurrences could not be booked (limit {self.max_skipped}); "
                               "nothing was saved", skipped=skipped)
            return {"series_id": series_id, "room_id": room_id, "user": user, "repeat": "weekly",
                    "repeat_until": until.isoformat(), "created": created, "skipped": skipped}

    # ---------- read ----------
    def get_booking(self, booking_id: int) -> dict:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if row is None:
            raise NotFound(f"booking {booking_id} does not exist")
        return self._booking_json(row)

    def list_bookings(self, room_id: Optional[int] = None, include_cancelled: bool = False) -> list:
        sql, args = "SELECT * FROM bookings WHERE 1=1", []
        if room_id is not None:
            sql += " AND room_id = ?"
            args.append(room_id)
        if not include_cancelled:
            sql += " AND status = 'active'"
        with self._read() as conn:
            rows = conn.execute(sql + " ORDER BY room_id, start_utc", args).fetchall()
        return [self._booking_json(r) for r in rows]

    def get_series(self, series_id: int) -> dict:
        with self._read() as conn:
            s = conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
            if s is None:
                raise NotFound(f"series {series_id} does not exist")
            rows = conn.execute("SELECT * FROM bookings WHERE series_id = ? ORDER BY start_utc",
                                (series_id,)).fetchall()
        return {"series_id": s["id"], "room_id": s["room_id"], "user": s["user"],
                "repeat": "weekly", "repeat_until": s["repeat_until"],
                "cancelled": s["cancelled_at"] is not None,
                "bookings": [self._booking_json(r) for r in rows]}

    # ---------- cancel ----------
    def cancel_booking(self, booking_id: int, scope: str = "instance") -> dict:
        """scope='instance' cancels just this booking; scope='series' cancels the whole
        series it belongs to (future instances only)."""
        if scope not in ("instance", "series"):
            raise InvalidInput("scope must be 'instance' or 'series'")
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
            if row is None:
                raise NotFound(f"booking {booking_id} does not exist")
            if scope == "series":
                if row["series_id"] is None:
                    raise InvalidInput("booking is not part of a series")
                return self._cancel_series(conn, row["series_id"])
            if row["status"] == "cancelled":
                return {"booking_id": booking_id, "cancelled": 0}        # idempotent
            if row["end_utc"] <= self._now_epoch():
                raise Conflict("booking is already over and cannot be cancelled")
            conn.execute("UPDATE bookings SET status='cancelled', cancelled_at=? WHERE id=?",
                         (self._now_epoch(), booking_id))
            return {"booking_id": booking_id, "cancelled": 1}

    def cancel_series(self, series_id: int) -> dict:
        with self._tx() as conn:
            return self._cancel_series(conn, series_id)

    def _cancel_series(self, conn, series_id: int) -> dict:
        if conn.execute("SELECT 1 FROM series WHERE id = ?", (series_id,)).fetchone() is None:
            raise NotFound(f"series {series_id} does not exist")
        now = self._now_epoch()
        # "Future" = has not started yet. Past and in-progress occurrences are left intact.
        cur = conn.execute(
            "UPDATE bookings SET status='cancelled', cancelled_at=? "
            "WHERE series_id=? AND status='active' AND start_utc >= ?", (now, series_id, now))
        conn.execute("UPDATE series SET cancelled_at = COALESCE(cancelled_at, ?) WHERE id=?",
                     (now, series_id))
        kept = conn.execute("SELECT COUNT(*) FROM bookings WHERE series_id=? AND status='active'",
                            (series_id,)).fetchone()[0]
        return {"series_id": series_id, "cancelled": cur.rowcount, "kept": kept}
