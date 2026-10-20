# DECISIONS

## (a) Decisions where the brief did not say

- **R1 vs R2, and R5.** Each recurring series is built in one transaction: up to 2 clashing occurrences are skipped and reported, and if more clash (or none can be booked) nothing is saved. I did not follow R5's "rooms 1 to N": the real ids are 3, 4, 9, 17, so availability iterates the rooms that exist.
- **Time model.** Timestamps are naive local ISO strings (C1), but UTC is kept internally for overlap checks. Recurrence steps the local date by 7 days and keeps the clock time (R3), the likely cause of Denver's "hour off" bookings. Offsets and `Z` are rejected. A DST-gap time is rejected for a single booking and skipped with a reason in a series; a time that happens twice uses the first occurrence.
- **Gaps I filled with assumptions.** Aurora and Basalt are in Berlin, Cinder and Dune in Denver. Cancelling any occurrence of a series cancels all future ones and keeps past and in-progress ones (`?scope=instance` cancels just one). Series are capped at 53 occurrences, ids and dates are range-checked, past bookings are allowed, and there is no auth.

## (b) Questions I would ask the PM

- Which office is each room in, and is the skip limit of 2 right?
- Who may cancel a series (there is no auth today)? Should cancelling a single occurrence exist, should past bookings be rejected, and should a DST-gap occurrence move to the next valid time instead of being skipped?

## (c) Where AI helped, and what it got wrong

- I used Claude to draft the design, code, tests and docs. I directed and verified it: I chose a REST API over a CLI, required the code to match the brief exactly, ran the 32 tests myself, tried the API by hand in `/docs` (a Denver series across the 1 Nov clock change stayed at 09:00), and ran the issue, branch and PR workflow. The AI also checked its own DST tests by breaking the recurrence logic on purpose; they failed, as they should.
- **Wrong, and corrected on my review:** (1) cancelling a recurring booking cancelled only one occurrence by default, against the brief; my exact-match requirement exposed it and the default was changed. (2) Huge ids and year-9999 dates returned a 500; found in my bug sweep, now a 422 with a test. (3) The AI used the wrong issue number in `Closes #N` (issues and PRs share one counter), so the issue did not auto-close; I noticed and closed it by hand.

## (d) Deliberately left out, and what is next

- Left out: auth, editing or moving bookings, repeat rules other than weekly, pagination, and notifications.
- Next: an admin role for cancelling series, Postgres with an exclusion constraint on room and time range as a second guard against double booking, and telling the series owner when occurrences were skipped.
