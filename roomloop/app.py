import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .service import BookingService, DomainError


class BookingIn(BaseModel):
    room_id: int
    start: str   # naive local ISO, e.g. 2026-07-02T09:00:00 (local to the room's office)
    end: str
    user: str


class RecurringIn(BookingIn):
    repeat: str = "weekly"
    repeat_until: str   # date, inclusive, e.g. 2026-12-28


def create_app(db_path: Optional[str] = None, service: Optional[BookingService] = None) -> FastAPI:
    svc = service or BookingService(db_path or os.environ.get("ROOMLOOP_DB", "roomloop.db"))
    app = FastAPI(title="RoomLoop", version="1.0")
    app.state.service = svc

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status,
                            content={"error": exc.code, "message": exc.message, **exc.extra})

    @app.get("/rooms")
    def rooms():
        return svc.list_rooms()

    @app.get("/availability")
    def availability(start: str, end: str):
        return svc.availability(start, end)

    @app.post("/bookings", status_code=201)
    def create_booking(body: BookingIn):
        return svc.create_booking(body.room_id, body.start, body.end, body.user)

    @app.post("/recurring-bookings", status_code=201)
    def create_recurring(body: RecurringIn):
        return svc.create_recurring(body.room_id, body.start, body.end, body.user,
                                    body.repeat, body.repeat_until)

    @app.get("/bookings")
    def list_bookings(room_id: Optional[int] = None, include_cancelled: bool = False):
        return svc.list_bookings(room_id, include_cancelled)

    @app.get("/bookings/{booking_id}")
    def get_booking(booking_id: int):
        return svc.get_booking(booking_id)

    @app.delete("/bookings/{booking_id}")
    def cancel_booking(booking_id: int, scope: str = "instance"):
        return svc.cancel_booking(booking_id, scope)

    @app.get("/series/{series_id}")
    def get_series(series_id: int):
        return svc.get_series(series_id)

    @app.delete("/series/{series_id}")
    def cancel_series(series_id: int):
        return svc.cancel_series(series_id)

    return app


# Run with: uvicorn roomloop.app:create_app --factory
# Database path comes from ROOMLOOP_DB (default ./roomloop.db).
