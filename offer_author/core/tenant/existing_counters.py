"""Counters (MA_O) that already exist on the offer, read before generating.

The stock Upload Rules path (rules-upload-model.component.ts) reuses an offer counter whenever the NAME matches, without
checking its type, creates the rest, and never deletes any. So the generator decides names knowing what is there:
  - reuse an existing counter only when name, level, data type, counter type and vector namespace all match AND the
    offer is in planning (no member holds a value yet, so the old counter is as good as new). Level matters: the
    dialog looks a counter up by name in the list of its own level (MA_O = OFFER, MA_A = ACTIVITY) and creates it when
    missing there, and bolapi keeps names unique per offer across levels (400 "offer, name must make a unique set");
  - otherwise pick a free name ("Total spend 2") and say so in the readback;
  - list the offer's counters the new rules do not use (never deleted here; the manager decides).
Where the counters come from: GET offers/{id}/ and offers/{id}/member-tracked-attributes/ on bolapi with the caller's
JWT (the calls the rules screen makes itself), or `existing_counters` in the request body (harnesses whose proxy strips
the JWT). Neither: unknown, and the readback says the counters were not checked.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...domain import Problem
from ..vocabulary import BOLAPI_LEVELS, OFFER_COUNTER

Counter = dict[str, Any]  # {name, level, data_type, counter_type, namespace}


def _status(x: Any) -> str:
    """'planning', or the status of an offer record ({status: 'planning'} / {status: {code: ...}} / {status_code})."""
    if isinstance(x, dict):
        s = x.get("status") or x.get("status_code")
        x = (s.get("code") or s.get("name")) if isinstance(s, dict) else s
    return str(x or "").lower()


def normalize(counters: Iterable[Mapping[str, Any]] | None) -> list[Counter]:
    """Counters as {name, level, data_type, counter_type, namespace}.

    Args:
        counters: Bolapi member-tracked-attributes records, or already-normalised dicts.

    Returns:
        One dict per counter, in order.
    """
    out = []
    for c in counters or []:
        ns = c.get("vector_namespace") or c.get("namespace")
        if not ns and isinstance(c.get("entity_attribute_map"), dict):
            ns = next((v[0] for v in c["entity_attribute_map"].values() if v), None)
        level = str(c.get("level") or OFFER_COUNTER).upper()
        out.append(
            {
                "name": c["name"],
                "level": BOLAPI_LEVELS.get(level, level),
                "data_type": (c.get("data_type") or "").upper(),
                "counter_type": (c.get("counter_type") or "REGULAR").upper(),
                "namespace": ns or None,
            }
        )
    return out


class Guard:
    """Decides counter names against the offer's existing counters and records what it did."""

    def __init__(self, existing: Mapping[str, Any]) -> None:
        """Guards one offer.

        Args:
            existing: {"offer_status": "planning", "counters": [bolapi records]}.
        """
        self.offer_status = _status(existing.get("offer_status") or "")
        self.planning = "planning" in self.offer_status
        self.existing = {c["name"]: c for c in normalize(existing.get("counters"))}
        self.reused: list[str] = []
        # name -> (the counter these rules wanted under it, why it was refused): a rename to report
        self.refused: dict[str, tuple[dict[str, Any], Problem]] = {}

    def _compare(
        self, name: str, data_type: str, counter_type: str, namespace: str | None, level: str | None
    ) -> tuple[Counter, dict[str, Any], bool]:
        """(the offer's counter of that name, the one these rules need, whether they are the same)."""
        have = self.existing[name]
        want = {
            "level": level or OFFER_COUNTER,
            "data_type": data_type,
            "counter_type": counter_type,
            "namespace": namespace or None,
        }
        return have, want, all(have[k] == v for k, v in want.items())

    def taken(
        self, name: str, data_type: str, counter_type: str, namespace: str | None, level: str | None = OFFER_COUNTER
    ) -> bool:
        """True when the generator must not use this name (recorded, with why); records a reuse when it may."""
        if name not in self.existing:
            return False
        _, want, same = self._compare(name, data_type, counter_type, namespace, level)
        if same and self.planning:
            if name not in self.reused:
                self.reused.append(name)
            return False
        self.refused[name] = (want, self.why(name, data_type, counter_type, namespace, level))
        return True

    def why(
        self, name: str, data_type: str, counter_type: str, namespace: str | None, level: str | None = OFFER_COUNTER
    ) -> Problem:
        """Why the offer's counter of that name cannot be used (the offer is live, or the counter differs)."""
        have, want, same = self._compare(name, data_type, counter_type, namespace, level)
        if same and not self.planning:
            return Problem.of("counter_on_live_offer", status=self.offer_status)
        return Problem.of("counter_differs", have=have, want=want)

    def report(self, counters: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
        """What the guard did, for the response and the readback.

        A refused name the rules do not use was renamed to the new counter of the same level and types.

        Args:
            counters: The ones the rules use (bolapi records or normalised).

        Returns:
            {checked, offer_status, reused, renamed: [{wanted, used, why}], created, unused}.
        """
        mine = normalize(counters)
        used = list(dict.fromkeys(c["name"] for c in mine))
        return {
            "checked": True,
            "offer_status": self.offer_status,
            "reused": [n for n in used if n in self.reused],
            "renamed": self._renames(mine, used),
            "created": [n for n in used if n not in self.existing],
            "unused": [n for n in self.existing if n not in used],
        }

    def _renames(self, mine: list[Counter], used: list[str]) -> list[dict[str, Any]]:
        """Each refused name the rules do not use, paired with a new counter of the level and types it was wanted as."""
        new = [c for c in mine if c["name"] not in self.existing]
        out: list[dict[str, Any]] = []
        for name, (want, why) in self.refused.items():
            free = [
                c["name"]
                for c in new
                if all(c[k] == v for k, v in want.items()) and c["name"] not in {r["used"] for r in out}
            ]
            if name not in used and free:
                out.append({"wanted": name, "used": free[0], "why": why})
        return out


def decode_rules_xml(v: str | None) -> str | None:
    """A saved canvas as bolapi and the screen keep it (base64 of the UTF-8 XML), or the XML itself, as XML text."""
    if not v:
        return None
    v = v.strip()
    if v.startswith("<"):
        return v
    try:
        return base64.b64decode(v).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


class BolapiCounterSource:
    """The offer's status and counters from bolapi with the caller's JWT."""

    def __init__(self, base_url: str, http: Any = None) -> None:
        """Reads from one bolapi (http: an httpx-like client, default httpx with a 20 s timeout)."""
        import httpx

        self.base = base_url.rstrip("/") + "/bolapi/v1/"
        self.http = http or httpx.Client(timeout=20)

    def get(self, offer_id: Any, authorization: str | None) -> dict[str, Any] | None:
        """{offer_status, counters, rules_xml?} of the offer; None without an offer or JWT, or when a read fails."""
        if offer_id is None or not authorization:
            return None
        h = {"Authorization": authorization}

        def fetch(rel: str) -> Any:
            return self.http.get(f"{self.base}offers/{offer_id}/{rel}", headers=h)

        with ThreadPoolExecutor(3) as pool:  # the three reads at once: they run before the model starts
            saved = pool.submit(self.rules_xml, offer_id, h)
            try:
                offer, mtos = pool.map(fetch, ("", "member-tracked-attributes/"))
                offer.raise_for_status()
                mtos.raise_for_status()
            except Exception:  # noqa: BLE001  (unknown is reported, generation goes on)
                return None
            body, x = mtos.json(), saved.result()
        return {
            "offer_status": offer.json(),
            "counters": body if isinstance(body, list) else body.get("results", []),
            **({"rules_xml": x} if x else {}),
        }

    def rules_xml(self, offer_id: Any, headers: Mapping[str, str]) -> str | None:
        """The offer's saved rules canvas (GET offers/{id}/drl/, what the rules screen loads), or None."""
        try:
            r = self.http.get(f"{self.base}offers/{offer_id}/drl/", headers=headers)
            r.raise_for_status()
            body = r.json()
            rows = body if isinstance(body, list) else body.get("results", [])
            return decode_rules_xml((rows[0] or {}).get("xml")) if rows else None
        except Exception:  # noqa: BLE001  (unknown: the run builds new rules)
            return None
