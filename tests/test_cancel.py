from conftest import BERLIN, book, recur

# Weekly Mondays 09:00-10:00 Berlin (CEST, UTC+2): 1, 8, 15, 22, 29 June 2026
SERIES = (BERLIN, "2026-06-01T09:00:00", "2026-06-01T10:00:00", "2026-06-29")


def _statuses(client, series_id):
    s = client.get(f"/series/{series_id}").json()
    return [(b["start"][:10], b["status"]) for b in s["bookings"]]


def test_cancel_series_keeps_past_cancels_future(client, clock):
    sid = recur(client, *SERIES).json()["series_id"]
    clock.set("2026-06-15T12:00:00")           # after the 15 June 09:00 local meeting
    r = client.delete(f"/series/{sid}")
    assert r.json() == {"series_id": sid, "cancelled": 2, "kept": 3}
    assert _statuses(client, sid) == [
        ("2026-06-01", "active"), ("2026-06-08", "active"), ("2026-06-15", "active"),
        ("2026-06-22", "cancelled"), ("2026-06-29", "cancelled")]
    assert client.get(f"/series/{sid}").json()["cancelled"] is True


def test_in_progress_occurrence_is_kept(client, clock):
    sid = recur(client, *SERIES).json()["series_id"]
    clock.set("2026-06-15T07:30:00")           # 09:30 Berlin: the 15 June meeting is under way
    client.delete(f"/series/{sid}")
    assert dict(_statuses(client, sid))["2026-06-15"] == "active"


def test_cancel_series_is_idempotent(client, clock):
    sid = recur(client, *SERIES).json()["series_id"]
    clock.set("2026-06-15T12:00:00")
    client.delete(f"/series/{sid}")
    assert client.delete(f"/series/{sid}").json()["cancelled"] == 0


def test_cancelled_series_frees_the_room(client, clock):
    sid = recur(client, *SERIES).json()["series_id"]
    clock.set("2026-06-15T12:00:00")
    client.delete(f"/series/{sid}")
    assert book(client, BERLIN, "2026-06-22T09:00:00", "2026-06-22T10:00:00").status_code == 201


def test_cancel_one_instance_on_request(client):
    created = recur(client, *SERIES).json()["created"]
    r = client.delete(f"/bookings/{created[1]['id']}", params={"scope": "instance"})
    assert r.json()["cancelled"] == 1
    assert [s for _, s in _statuses(client, created[0]["series_id"])].count("cancelled") == 1


def test_cancelling_a_recurring_booking_defaults_to_all_future_instances(client, clock):
    # The brief: cancelling a recurring booking cancels all future instances, past ones stay.
    created = recur(client, *SERIES).json()["created"]
    clock.set("2026-06-15T12:00:00")
    r = client.delete(f"/bookings/{created[0]['id']}")        # a past occurrence, no scope given
    assert r.json() == {"series_id": created[0]["series_id"], "cancelled": 2, "kept": 3}
    assert [s for _, s in _statuses(client, created[0]["series_id"])] == [
        "active", "active", "active", "cancelled", "cancelled"]


def test_cancel_errors(client, clock):
    assert client.delete("/bookings/999").status_code == 404
    assert client.delete("/series/999").status_code == 404
    single = book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").json()
    assert client.delete(f"/bookings/{single['id']}", params={"scope": "series"}).status_code == 422
    assert client.delete(f"/bookings/{single['id']}", params={"scope": "nope"}).status_code == 422
    clock.set("2026-08-01T00:00:00")           # booking is long over
    assert client.delete(f"/bookings/{single['id']}").status_code == 409


def test_cancelling_twice_is_harmless(client):
    b = book(client, BERLIN, "2026-07-02T09:00:00", "2026-07-02T10:00:00").json()
    client.delete(f"/bookings/{b['id']}")
    assert client.delete(f"/bookings/{b['id']}").json()["cancelled"] == 0
