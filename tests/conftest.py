import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from roomloop.app import create_app          # noqa: E402
from roomloop.service import BookingService  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "test_data"

# Seeded rooms (ids are deliberately not sequential): 3 Aurora/Berlin, 4 Basalt/Berlin,
# 9 Cinder/Denver, 17 Dune/Denver.
BERLIN, BERLIN_2, DENVER, DENVER_2 = 3, 4, 9, 17


class Clock:
    """Movable 'now' so tests can put time in the middle of a series."""

    def __init__(self, iso_utc: str = "2026-01-01T00:00:00"):
        self.set(iso_utc)

    def set(self, iso_utc: str):
        self.t = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock):
    return BookingService(str(tmp_path / "test.db"), now=clock)


@pytest.fixture
def client(svc):
    return TestClient(create_app(service=svc))


@pytest.fixture
def dst_data():
    return json.loads((DATA / "dst_scenarios.json").read_text())


def utc_hours(svc, booking_ids):
    """UTC hour of each booking's start, read straight from storage."""
    conn = sqlite3.connect(svc.db_path)
    try:
        out = []
        for bid in booking_ids:
            (epoch,) = conn.execute("SELECT start_utc FROM bookings WHERE id=?", (bid,)).fetchone()
            out.append(datetime.fromtimestamp(epoch, timezone.utc).hour)
        return out
    finally:
        conn.close()


def count(svc, table):
    conn = sqlite3.connect(svc.db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def book(client, room, start, end, user="u"):
    return client.post("/bookings", json={"room_id": room, "start": start, "end": end, "user": user})


def recur(client, room, start, end, until, user="u", **extra):
    return client.post("/recurring-bookings", json={
        "room_id": room, "start": start, "end": end, "user": user,
        "repeat": "weekly", "repeat_until": until, **extra})
