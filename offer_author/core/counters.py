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


def _status(x):
    """'planning', or the status of an offer record ({status: 'planning'} / {status: {code: ...}} / {status_code})."""
    if isinstance(x, dict):
        s = x.get("status") or x.get("status_code")
        x = (s.get("code") or s.get("name")) if isinstance(s, dict) else s
    return str(x or "").lower()


LEVELS = {"OFFER": "MA_O", "ACTIVITY": "MA_A", "TIER": "MA_TO", "TIER-ACTIVITY": "MA_A"}  # bolapi level -> block level


def normalize(counters):
    """bolapi member-tracked-attributes records (or already-normalised dicts) -> [{name, level, data_type, counter_type,
    namespace}]."""
    out = []
    for c in counters or []:
        ns = c.get("vector_namespace") or c.get("namespace")
        if not ns and isinstance(c.get("entity_attribute_map"), dict):
            ns = next((v[0] for v in c["entity_attribute_map"].values() if v), None)
        level = str(c.get("level") or "MA_O").upper()
        out.append({"name": c["name"], "level": LEVELS.get(level, level), "data_type": (c.get("data_type") or "").upper(),
                    "counter_type": (c.get("counter_type") or "REGULAR").upper(), "namespace": ns or None})
    return out


class Guard:
    """Decides counter names against the offer's existing counters and records what it did."""

    def __init__(self, existing):
        """existing: {"offer_status": "planning", "counters": [bolapi records]}."""
        self.offer_status = _status(existing.get("offer_status") or "")
        self.planning = "planning" in self.offer_status
        self.existing = {c["name"]: c for c in normalize(existing.get("counters"))}
        self.reused, self.renamed = [], []

    def taken(self, name, data_type, counter_type, namespace, level="MA_O"):
        """True when the generator must not use this name; records a reuse when it may."""
        have = self.existing.get(name)
        if have is None:
            return False
        want = {"level": level or "MA_O", "data_type": data_type, "counter_type": counter_type, "namespace": namespace or None}
        same = all(have[k] == v for k, v in want.items())
        if same and self.planning:
            if name not in self.reused:
                self.reused.append(name)
            return False
        return True

    def why(self, name, data_type, counter_type, namespace, level="MA_O"):
        have = self.existing[name]
        want = {"level": level or "MA_O", "data_type": data_type, "counter_type": counter_type, "namespace": namespace or None}
        if not self.planning and all(have[k] == v for k, v in want.items()):
            return f"the offer is in {self.offer_status or 'an unknown status'}, so members may already hold values in it"
        kind = lambda d: f"{d['level']} {d['data_type']} {d['counter_type']}" + (f" per {d['namespace']}" if d.get("namespace") else "")
        return f"the offer's counter is {kind(have)}, these rules need {kind(want)}"

    def report(self, used):
        """-> {checked, offer_status, reused, renamed, created, unused} for the response and the readback."""
        used = list(dict.fromkeys(used))
        return {"checked": True, "offer_status": self.offer_status, "reused": [n for n in used if n in self.reused],
                "renamed": self.renamed, "created": [n for n in used if n not in self.existing],
                "unused": [n for n in self.existing if n not in used]}


def notes(report):
    """Readback lines for the canvas note."""
    if not report:
        return []
    if not report.get("checked"):
        return ["The offer's existing counters were not checked (no access to them); if it has counters with these names, "
                "the rules screen reuses them by name."]
    out = [f"Counter '{r['used']}' is new: '{r['wanted']}' already exists on this offer and {r['why']}." for r in report["renamed"]]
    if report["reused"]:
        out.append("Reuses the offer's existing counter(s): " + ", ".join(f"'{n}'" for n in report["reused"]) + ".")
    if report["unused"]:
        out.append("Not used by these rules (delete them on the rules screen if no longer needed): "
                   + ", ".join(f"'{n}'" for n in report["unused"]) + ".")
    return out


class BolapiCounterSource:
    """The offer's status and counters from bolapi with the caller's JWT."""

    def __init__(self, base_url, http=None):
        import httpx
        self.base = base_url.rstrip("/") + "/bolapi/v1/"
        self.http = http or httpx.Client(timeout=20)

    def get(self, offer_id, authorization):
        if offer_id is None or not authorization:
            return None
        h = {"Authorization": authorization}
        try:
            offer = self.http.get(f"{self.base}offers/{offer_id}/", headers=h)
            mtos = self.http.get(f"{self.base}offers/{offer_id}/member-tracked-attributes/", headers=h)
            offer.raise_for_status()
            mtos.raise_for_status()
        except Exception:  # noqa: BLE001  (unknown is reported, generation goes on)
            return None
        body = mtos.json()
        return {"offer_status": offer.json(), "counters": body if isinstance(body, list) else body.get("results", [])}
