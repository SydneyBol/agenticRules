"""When a run may end: the checks a finish must pass."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ...domain import Problem, ToolInput

if TYPE_CHECKING:
    from .run import Run


def finish_blockers(run: Run, parts: Sequence[str], x: ToolInput) -> list[Problem]:
    """Why a finish is sent back.

    A canvas whose last compose had problems, Offer Main not set when it is part of the job, a segment without a
    name, or an Offer Main segment_prompt naming members with no segment built; only the first holds when the model
    is asking something or reporting something it cannot build.

    Args:
        run: The run.
        parts: The parts of the offer the run builds.
        x: The finish fields.

    Returns:
        The blockers; empty when the run may end.
    """
    out = run.canvases.pending()
    asking = x.get("blocking_questions") or x.get("unsupported")
    if "offer_main" in parts and run.offer_main.value is None and not asking:
        out.append(Problem.of("offer_main_not_set"))
    segment = run.canvases.last.get("segment")
    if "segment" in parts and segment and not (x.get("segment_name") or "").strip() and not asking:
        out.append(Problem.of("segment_name_empty"))
    # Select Members builds its segment from Offer Main's segment_prompt: one naming members needs a built segment
    named = ((run.offer_main.value or {}).get("segment_prompt") or "").strip()
    if "segment" in parts and named and not (segment and segment.built) and not asking:
        out.append(Problem.of("segment_prompt_without_segment"))
    return out
