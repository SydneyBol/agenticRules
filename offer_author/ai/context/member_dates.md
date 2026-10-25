# Windows around a member's yearly date (birthday, wedding anniversary) — engine behaviour

Hand-written (not generated). Checked against `common/.../DateUtils.dateToField` and engine runs, 2026-10-25.

- A date part read from a BIT or member date (`YEAR`, `MONTH`, `DAY_OF_YEAR`, `WEEK_OF_YEAR`, ...) is an integer in the
  program time zone. A member date that is missing or cannot be parsed reads as **-1** in every part. So a window
  around a member date also needs a condition that the member date part is >= 1, or a member without that date can
  match it (for example, -1 falls inside a window that crosses New Year).
- No block gives the number of days between two dates, or a member date moved to the BIT's year. A window of N days
  before or after a yearly member date, in day-of-year terms, must wrap at the year end. A spend on 21 Dec is 15 days
  before a 5 Jan birthday.
  - offset = (BIT DAY_OF_YEAR − member DAY_OF_YEAR + 365 + N) % 365 (arithmetic blocks);
  - the spend is inside the window when offset <= 2N.
  - `member − N <= BIT <= member + N` is wrong for every window that crosses 31 Dec / 1 Jan.
  - In a leap year the day of year after 28 Feb is one higher, so a window can be off by one day there.
- "Once per birthday" (or anniversary) is once per occurrence, not per calendar year: a 21 Dec spend and a 4 Jan
  spend belong to the same 5 Jan birthday. Offer Main limits have no yearly frequency, so the limit needs an offer
  counter.
  - day index = BIT YEAR × 366 + BIT DAY_OF_YEAR. It grows by 1 a day, by 2 at a year end.
  - Within one window, two spends are at most 2N + 1 apart in this index; the next occurrence's window is more than
    300 later.
  - Reward only when the counter is 0 or day index − counter > 2N + 1, and SET the counter to the day index when
    rewarding.
- A month-based condition ("in the birthday month") compares MONTH parts and needs neither.
