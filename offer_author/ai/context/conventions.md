# Usual readings of loyalty terms

How managers' words are usually meant, when the description does not say otherwise. A tenant's earlier offers,
when the message shows them, may show that tenant's own habit; the description itself always wins.

- "spend", "spends", "spending": the activity type labelled exactly "Spend". Use another type only when the manager
  names it. When no activity is named, use Spend and record that as an assumption.
- "Nx points": points = the measure x N in total (not N extra on top). Record that as an assumption.
- An offer gives only what it describes: a transaction that does not qualify earns nothing from this offer ("double
  points on Sundays" pays nothing on a Monday; the program's base earning is not part of the offer).
- "over X", "more than X": strictly greater (>). "at least X", "X or more": greater or equal (>=).
- "reaches X", "hits X", "once the total is X": greater or equal (>=), on the transaction that brings it to X ($300
  then $200 reaches $500 on the second).
- DAY_OF_WEEK numbers run Monday=1 to Sunday=7 (the engine's ISO numbering). HOUR_OF_DAY is 0-23.
- "in the birthday month", "on weekends", "during December", "between 5 and 7 pm": when the transaction happened,
  i.e. the BIT date (`h_bit_date`), never the current date block.
- Anything that must carry over from one transaction to the next (counts, totals, "once", caps, unlocks, distinct
  values) is an offer counter (MA_O): MA_A values live for one transaction only (see how offers and blocks run: counters).
- "a maximum of N", "up to N", "capped at N" points: the cap holds exactly. The award that would pass it is trimmed
  to what is left (N minus the counter so far), the counter ends at N, and later transactions earn nothing more from
  that rule.
- "every Nth", "each Nth": the counter changes exactly once per counted transaction. A counter update is seen by the
  conditions after it in the same transaction, so a branch that rewards and resets the count must not be followed by
  a branch that also counts the same transaction: write one update (count = (count + 1) mod N, rewarding when the
  count before it is N - 1) or make the branches exclusive.
