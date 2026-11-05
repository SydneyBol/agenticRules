"""Generation jobs: one agent run per prompt, run in the background and kept for the screens that ask for it.

The stock screens ask more than once for the same answer (the GenAI popup re-posts while it hears "Generating", the
rules screen asks again when it opens), so a running job, or one that succeeded a moment ago, answers them instead of
a new run. Jobs live in memory (a real deployment would use Redis or DynamoDB).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("offer_author.pulsar")

HOLD_S = 20  # hold a regenerate=true request this long before answering "Generating" (the popup re-posts at once)
WAIT_CACHED_S = 45  # regenerate=false: the rules screen does not poll, so wait for a running job before giving up
RECENT_S = 120  # a finished job this recent answers the popup's re-post instead of starting a new generation
KEEP_S = 86400  # a finished job is dropped from memory this long after it ended

Key = tuple[str, str]


@dataclass
class Job:
    """One agent run in the background.

    Attributes:
        prompt: The prompt it runs.
        state: running or done.
        done: Set once it ended.
        main: Set once Offer Main is known (or the run ended without it).
        offer_main: Offer Main, as soon as the agent set it.
        result: The run's result (worded), once done.
        done_at: When it ended.
    """

    prompt: str = ""
    state: str = "running"
    done: threading.Event = field(default_factory=threading.Event)
    main: threading.Event = field(default_factory=threading.Event)
    offer_main: dict[str, Any] | None = None
    result: dict[str, Any] = field(default_factory=dict)
    done_at: float = 0.0

    def recent(self) -> bool:
        """Running, or ended within RECENT_S."""
        return self.state == "running" or time.time() - self.done_at < RECENT_S

    def wait(self, seconds: float) -> bool:
        """Waits up to seconds for the run to end. Returns whether it ended."""
        return self.done.wait(seconds)


def job_summary(res: dict[str, Any]) -> str:
    """One log line's worth of a finished run: status, latency, repairs, tokens and where the time went."""
    d = res.get("diagnostics") or {}
    u, tm = d.get("usage") or {}, d.get("timing") or {}

    def js(v: Any) -> str:
        return json.dumps(v or {}, separators=(",", ":"))

    return (
        f"status={res.get('status')} latency_s={d.get('latency_s')} repairs={d.get('repairs')} "
        f"rules={(res.get('rules') or {}).get('rule_count')} model={d.get('model')} turns={d.get('turns')} "
        f"tokens_in={u.get('input_tokens')} tokens_out={u.get('output_tokens')} "
        f"cache_read={u.get('cache_read_input_tokens')} cache_write={u.get('cache_creation_input_tokens')} "
        f"model_s={tm.get('model_s')} tools_s={js(tm.get('tools_s'))} input_chars={js(tm.get('input_chars'))} "
        f"per_turn={json.dumps(tm.get('turns') or [], separators=(',', ':'))}"
        + (f" earlier={js(d['earlier'])}" if d.get("earlier") else "")
        + (f" plan={js(d['plan'])}" if d.get("plan") else "")
        + (
            f" verify={js({k: v for k, v in d['verify'].items() if k not in ('repair', 'evidence')})}"
            if d.get("verify")
            else ""
        )
    )


def job_problems(res: dict[str, Any]) -> str:
    """What sent the run round again, per turn, for the log.

    Compose problems and errors: the tool results the model read.
    """
    out = []
    for t in (res.get("diagnostics") or {}).get("trace") or []:
        why = (t.get("problems") or []) + ([t["error"]] if t.get("error") else [])
        if why:
            out.append({"turn": t.get("turn"), "tool": t.get("tool"), "why": [str(w)[:200] for w in why[:4]]})
    return json.dumps(out, separators=(",", ":"))


def _log_finished(key: Key, res: dict[str, Any]) -> None:
    log.info("pulsar job offer=%s %s problems=%s", key[0], job_summary(res), job_problems(res))
    verify = (res.get("diagnostics") or {}).get("verify") or {}
    if verify.get("evidence"):  # the agent's test cases and every check's transactions with the engine's answers
        log.info("pulsar cases offer=%s %s", key[0], json.dumps(verify["evidence"], separators=(",", ":"), default=str))
    if verify.get("repair"):  # the repair's evidence: both checks' mismatches, the message, the canvases before it
        log.info("pulsar repair offer=%s %s", key[0], json.dumps(verify["repair"], separators=(",", ":"), default=str))
    trace = (res.get("diagnostics") or {}).get("trace") or []
    if any("input" in t for t in trace):  # Settings.trace_inputs on
        log.info("pulsar job offer=%s trace=%s", key[0], json.dumps(trace, separators=(",", ":")))


class JobStore:
    """Per-offer prompts and generation jobs.

    Attributes:
        prompts: (offer id, stage) -> {prompt, offer_status}: each screen's saved prompt.
        jobs: (offer id | "offer" | "segment", prompt hash) -> its job.
        latest: offer id -> key of its latest finished rules job.
        offer_of: offer id -> key of the run of its Offer Main prompt.
    """

    def __init__(self) -> None:
        """Starts empty."""
        self.lock = threading.Lock()
        self.prompts: dict[tuple[str, str], dict[str, Any]] = {}
        self.jobs: dict[Key, Job] = {}
        self.latest: dict[str, Key] = {}
        self.offer_of: dict[str, Key] = {}

    @staticmethod
    def key(owner: Any, prompt: str) -> Key:
        """A job's key: its owner (an offer id, or "offer" / "segment") and its prompt's hash."""
        return (str(owner), hashlib.sha256(prompt.strip().encode()).hexdigest()[:16])

    def offer_for(self, offer_id: Any) -> Job | None:
        """The run of the offer's Offer Main prompt, if there was one."""
        k = self.offer_of.get(str(offer_id))
        return self.jobs.get(k) if k else None

    def offer_with_segment(self, prompt: str) -> Job | None:
        """The offer run whose segment prompt this is (the Select Members screen shows it)."""
        if not prompt.strip():
            return None
        return next(
            (
                j
                for j in list(self.jobs.values())
                if (j.offer_main or {}).get("segment_prompt", "").strip() == prompt.strip()
            ),
            None,
        )

    def start(self, k: Key, work: Callable[[Job], dict[str, Any]], track: bool = True, prompt: str = "") -> Job:
        """The job for k: a running one, or one that succeeded within RECENT_S; else a new one running work(job).

        Args:
            k: The job's key.
            work: Runs the agent for the job and returns its result; it may set job.offer_main as soon as it is known.
            track: The job is its offer's latest rules job once it ends.
            prompt: The prompt it runs.

        Returns:
            The job.
        """
        with self.lock:
            self._evict()
            job = self.jobs.get(k)
            if job and (job.state == "running" or (job.recent() and job.result.get("status") != "error")):
                return job
            job = self.jobs[k] = Job(prompt=prompt.strip())

        def run() -> None:
            try:
                job.result = work(job)
            except Exception as e:  # the screen gets the error; the service goes on
                log.exception("pulsar job offer=%s failed", k[0])
                job.result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
            job.done_at, job.state = time.time(), "done"  # done_at first: a reader that sees "done" has both
            _log_finished(k, job.result)
            if track:
                with self.lock:
                    self.latest[k[0]] = k
            job.main.set()
            job.done.set()

        threading.Thread(target=run, daemon=True).start()
        return job

    def _evict(self) -> None:
        """Drops jobs that ended more than KEEP_S ago, and what points at them (called under the lock)."""
        old = [k for k, j in self.jobs.items() if j.state == "done" and time.time() - j.done_at > KEEP_S]
        for k in old:
            del self.jobs[k]
        for refs in (self.latest, self.offer_of):
            for oid in [o for o, k in refs.items() if k in old]:
                del refs[oid]
