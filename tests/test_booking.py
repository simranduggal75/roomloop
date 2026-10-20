from concurrent.futures import ThreadPoolExecutor

from conftest import BERLIN, BERLIN_2, DENVER, book
from roomloop.service import Conflict


def test_rooms_response_is_exactly_the_c2_shape(client):
    rooms = client.get("/rooms").json()
    assert rooms == [
        {"id": 3, "name": "Aurora", "capacity": 8},
        {"id": 4, "name": "Basalt", "capacity": 4},
        {"id": 9, "name": "Cinder", "capacity": 12},
        {"id": 17, "name": "Dune", "capacity": 6},
    ]  # no extra keys (e.g. office/timezone): the dashboard renders whatever comes back


def test_timestamps_are_naive_local_iso(client):
    b = book(client, DENVER, "2026-07-02T09:00:00", "2026-07-02T10:00:00").json()
    assert b["start"] == "2026-07-02T09:00:00" and b["end"] == "2026-07-02T10:00:00"  # C1
    assert client.get(f"/bookings/{b['id']}").json()["start"] == "2026-07-02T09:00:00"


def test_back_to_back_is_not_a_conflict(client):
    assert book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").status_code == 201
    assert book(client, BERLIN, "2026-07-02T10:00:00", "2026-07-02T11:00:00").status_code == 201
    assert book(client, BERLIN, "2026-07-02T08:00:00", "2026-07-02T09:00:00").status_code == 201


def test_overlaps_are_conflicts(client):
    first = book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T11:00:00").json()
    for s, e in [("08:00", "09:01"), ("10:59", "12:00"), ("09:30", "10:30"),
                 ("08:00", "12:00"), ("09:00", "11:00")]:
        r = book(client, BERLIN, f"2026-07-02T{s}:00", f"2026-07-02T{e}:00")
        assert r.status_code == 409, (s, e)
        assert r.json()["conflicts_with"] == first["id"]


def test_same_time_different_room_is_fine(client):
    assert book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").status_code == 201
    assert book(client, BERLIN_2, "2026-07-02T09:00:00", "2026-07-02T10:00:00").status_code == 201


def test_validation(client):
    ok = ("2026-07-02T09:00:00", "2026-07-02T10:00:00")
    assert book(client, BERLIN, ok[1], ok[0]).status_code == 422                 # end before start
    assert book(client, BERLIN, ok[0], ok[0]).status_code == 422                 # zero length
    assert book(client, BERLIN, "2026-07-02T09:00:00Z", ok[1]).status_code == 422  # 'Z' rejected
    assert book(client, BERLIN, "2026-07-02T09:00:00+02:00", ok[1]).status_code == 422
    assert book(client, BERLIN, "tomorrow", ok[1]).status_code == 422
    assert book(client, BERLIN, *ok, user="  ").status_code == 422
    assert book(client, 1, *ok).status_code == 404                                # 1 is not a real room id
    assert client.post("/bookings", json={"room_id": 3}).status_code == 422       # missing fields


def test_ambiguous_fall_back_time_uses_first_occurrence(svc, client):
    # 01:30 on 1 Nov 2026 happens twice in Denver; we pick the first (MDT = 07:30Z).
    b = book(client, DENVER, "2026-11-01T01:30:00", "2026-11-01T02:30:00").json()
    from conftest import utc_hours
    assert utc_hours(svc, [b["id"]]) == [7]


def test_nonexistent_spring_forward_time_is_rejected(client):
    r = book(client, DENVER, "2026-03-08T02:30:00", "2026-03-08T03:30:00")
    assert r.status_code == 422


def test_cancelled_booking_frees_the_slot(client):
    b = book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").json()
    assert client.delete(f"/bookings/{b['id']}").json() == {"booking_id": b["id"], "cancelled": 1}
    assert book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").status_code == 201


def test_availability_iterates_real_room_ids(client):
    book(client, DENVER, "2026-07-02T09:00:00", "2026-07-02T10:00:00")
    free = client.get("/availability", params={"start": "2026-07-02T09:00:00",
                                               "end": "2026-07-02T10:00:00"}).json()
    assert [r["id"] for r in free] == [3, 4, 17]    # includes ids 4, 17; never probes 1, 2, 5...


def test_concurrent_requests_cannot_double_book(svc):
    def attempt(i):
        try:
            svc.create_booking(BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00", f"user{i}")
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1
