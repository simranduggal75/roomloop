import json

import pytest

from conftest import BERLIN, DENVER, DATA, book, count, recur, utc_hours
from roomloop.service import BookingService

_SCENARIOS = json.loads((DATA / "dst_scenarios.json").read_text())["scenarios"]


@pytest.mark.parametrize("sc", _SCENARIOS, ids=lambda s: s["name"])
def test_same_wall_clock_time_across_dst(sc, svc, client):
    r = recur(client, sc["room_id"], sc["start"], sc["end"], sc["repeat_until"])
    assert r.status_code == 201
    created = r.json()["created"]
    assert [b["start"] for b in created] == sc["expected_local_starts"]   # R3: always 09:00 local
    assert [b["end"][11:] for b in created] == ["10:00:00"] * len(created)
    assert utc_hours(svc, [b["id"] for b in created]) == sc["expected_utc_hours"]  # UTC shifts at DST


def test_nonexistent_dst_time_is_skipped_not_fatal(dst_data, client):
    g = dst_data["spring_forward_gap"]
    r = recur(client, g["room_id"], g["start"], g["end"], g["repeat_until"])
    assert r.status_code == 201
    body = r.json()
    assert [b["start"] for b in body["created"]] == g["expected_created_starts"]
    assert [{"start": s["start"], "reason": s["reason"]} for s in body["skipped"]] == g["expected_skipped"]


# Weekly Mondays 09:00 Berlin: 1, 8, 15, 22, 29 June 2026
SERIES = (BERLIN, "2026-06-01T09:00:00", "2026-06-01T10:00:00", "2026-06-29")


def _block(client, day):
    assert book(client, BERLIN, f"2026-06-{day:02d}T09:30:00", f"2026-06-{day:02d}T10:30:00",
                "blocker").status_code == 201


def test_one_conflict_is_skipped_rest_created(client):
    _block(client, 8)
    r = recur(client, *SERIES)
    assert r.status_code == 201
    body = r.json()
    assert [b["start"][:10] for b in body["created"]] == ["2026-06-01", "2026-06-15", "2026-06-22", "2026-06-29"]
    assert [(s["start"][:10], s["reason"]) for s in body["skipped"]] == [("2026-06-08", "conflict")]


def test_two_conflicts_are_still_skipped(client):
    _block(client, 8)
    _block(client, 22)
    r = recur(client, *SERIES)
    assert r.status_code == 201
    assert len(r.json()["created"]) == 3 and len(r.json()["skipped"]) == 2


def test_three_conflicts_fail_and_save_nothing(svc, client):
    for d in (8, 15, 22):
        _block(client, d)
    before = (count(svc, "bookings"), count(svc, "series"))
    r = recur(client, *SERIES)
    assert r.status_code == 409 and len(r.json()["skipped"]) == 3
    assert (count(svc, "bookings"), count(svc, "series")) == before          # R1: nothing saved


def test_all_conflicting_fails(svc, client):
    for d in (1, 8, 15, 22, 29):
        _block(client, d)
    before = count(svc, "bookings")
    assert recur(client, *SERIES).status_code == 409
    assert count(svc, "bookings") == before and count(svc, "series") == 0


def test_failure_midway_rolls_back_everything(svc, monkeypatch):
    real = BookingService._insert
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk exploded")
        return real(*a, **kw)

    monkeypatch.setattr(BookingService, "_insert", staticmethod(flaky))
    with pytest.raises(RuntimeError):
        svc.create_recurring(*SERIES[:3], "u", "weekly", SERIES[3])
    assert count(svc, "bookings") == 0 and count(svc, "series") == 0        # R1


def test_series_validation(client):
    assert recur(client, BERLIN, "2026-06-08T09:00:00", "2026-06-08T10:00:00", "2026-06-01").status_code == 422
    assert recur(client, BERLIN, *SERIES[1:3], "2026-06-29", repeat="daily").status_code == 422
    assert recur(client, BERLIN, *SERIES[1:3], "not-a-date").status_code == 422
    assert recur(client, BERLIN, *SERIES[1:3], "2030-06-29").status_code == 422   # > 53 occurrences
    assert recur(client, 1, *SERIES[1:3], "2026-06-29").status_code == 404


def test_series_can_be_inspected(client):
    sid = recur(client, *SERIES).json()["series_id"]
    s = client.get(f"/series/{sid}").json()
    assert len(s["bookings"]) == 5 and s["cancelled"] is False
