"""The tools the model calls, as LangChain tools.

Each tool is one `@tool` function: its docstring is the description the model reads, its `args_schema` (a Pydantic
model below) the input it may send, and its body one call into the run's tools as code (`core.run.Run`), reached
through `ToolRuntime.context` (a `RunContext`). The result goes back to the model worded (render.py); the facts stay on
the message as its artifact, for the graph's judge (graph.py). The block tree stays a free object: the toolbox grammar
(block types, inputs, connection checks, catalog codes) is data from the stock UI's block sources, checked by
core/canvas/checks/.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, cast

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolInvocationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..core.run import Run
from ..core.settings import Settings
from ..core.vocabulary import COUNTER_LEVELS, OFFER_MAIN_FORM
from ..domain import Result, ToolInput
from . import render

log = logging.getLogger("offer_author.tools")

MAX_RESULT_CHARS = 60000

Part = Annotated[
    Literal["offer_main", "segment", "rules"],
    Field(description="the part it is about; each screen's API returns only its own part's items"),
]


def _shown_required(*names: str) -> ConfigDict:
    """A model config whose JSON schema, as the model is shown it, requires these fields.

    Validation stays as lenient as the code: an item naming no part, or a plain string, belongs to every part of the
    job (core.run.notes_by_part).
    """
    return ConfigDict(json_schema_extra=lambda schema, _: schema.update(required=list(names)))


class Note(BaseModel):
    """An assumption or a blocking question, for one part of the job or all of it."""

    model_config = _shown_required("part", "text")
    part: Part | None = None
    text: str

    @model_validator(mode="before")
    @classmethod
    def _text_only(cls, v: Any) -> Any:
        """A plain string is a note for every part."""
        return {"text": v} if isinstance(v, str) else v


class Unsupported(BaseModel):
    """A phrase of the request the blocks cannot express, and why."""

    model_config = _shown_required("part", "phrase", "reason")
    part: Part | None = None
    phrase: str
    reason: str
    alternative: str | None = None


class Counter(BaseModel):
    """An offer or activity counter the blocks refer to."""

    name: str
    level: Literal[tuple(COUNTER_LEVELS)] | None = None  # type: ignore[valid-type]
    data_type: Literal["NUMBER", "TEXT", "DATE", "BOOLEAN"]
    counter_type: Literal["REGULAR", "VECTOR"]
    target_type: str | None = None
    namespace: str | None = Field(None, description="VECTOR only: the attribute it counts per (e.g. h_location)")
    entity_level: str | None = Field(None, description="VECTOR only; default Activity")


class Edit(BaseModel):
    """One change to a canvas's last tree, by block id; fields and inputs may be named directly."""

    model_config = ConfigDict(extra="allow")
    id: str
    fields: dict[str, Any] | None = None
    picked: str | None = None
    replace: dict[str, Any] | None = None
    remove: bool | None = None
    insert_after: list[dict[str, Any]] | dict[str, Any] | None = None
    insert_before: list[dict[str, Any]] | dict[str, Any] | None = None
    append: dict[str, list[dict[str, Any]]] | None = None


Readback = Annotated[str, Field(description="Plain-English description of what the offer will do")]
SegmentName = Annotated[str, Field(description="under 50 characters, when a segment was built")]


class LookupValues(BaseModel):
    """Input of lookup_values."""

    object: Literal["activity", "member", "product"]
    attribute: str
    text: str = Field(description="The manager's words for ONE value")


class SetOfferMain(BaseModel):
    """Input of set_offer_main: the Offer Main form."""

    offer_name: str = Field(description=f"at most {OFFER_MAIN_FORM['offer_name']['max_length']} characters")
    subtitle: str
    offer_description: str
    terms: list[str]
    start_date: str = Field(description="YYYY-MM-DD the manager stated, else empty")
    end_date: str = Field(description="YYYY-MM-DD the manager stated (last day included), else empty")
    max_usage_per_user: int = Field(
        description="how often ONE member can earn it in one frequency period (Offer Main holds one such limit); "
        "0 = unlimited"
    )
    frequency: Literal[("none", *OFFER_MAIN_FORM["frequency"]["choices"])]  # type: ignore[valid-type]
    num_of_clients: int = Field(description="total earnings across all members; 0 = unlimited")
    offer_frequency_type: Literal[("", *OFFER_MAIN_FORM["offer_frequency_type"]["choices"])]  # type: ignore[valid-type]
    segment_prompt: str = Field(description="who the offer is for, in the manager's words; empty = every member")
    rules_prompt: str = Field(
        description="the rules part in the manager's words, without what Offer Main or the segment covers"
    )


class Canvas(BaseModel):
    """Input of compose: a whole block tree, or edits and loose blocks on the canvas's last one."""

    canvas: Literal["rules", "segment"]
    blocks: dict[str, Any] | None = Field(
        None, description="The block tree: one offer_generic (rules) or targeting_holder (segment) root"
    )
    defs: dict[str, Any] | None = Field(
        None,
        description="Blocks used in more than one place, written once by name: "
        "{name: block | [blocks]}. In the tree (or another def) write "
        '{"use": name} where it goes; the tools copy it into each '
        "place. Kept for later composes of this canvas",
    )
    counters: list[Counter] | None = Field(
        None,
        description="The offer counters the blocks refer to (with edits: kept from the last compose unless given)",
    )
    edits: list[Edit] | None = Field(
        None,
        description="Instead of blocks: changes to this canvas's last tree (or the "
        "saved canvas shown in the message), by block id (rule#b9 -> b9). "
        "{id, <field>: value} sets fields (and inputs) as in a block; {id, "
        "picked}; {id, replace: block}; {id, remove: true}; {id, "
        "insert_after: [blocks]} / {id, insert_before: [blocks]} in its "
        "statement list; {id, append: {INPUT: [blocks]}} to the end of a "
        "statement input. Blocks no edit names stay exactly as they are",
    )
    loose: list[dict[str, Any]] | None = Field(
        None,
        description="Blocks put on the canvas on their own, not attached to "
        "anything (written disabled, so they add nothing to the "
        "rules): a block the manager asks for by itself. A block "
        "you attach where it cannot plug in is moved here by the "
        "tools, and the result says why",
    )


class Finish(BaseModel):
    """Input of finish: what the manager reads with the offer."""

    readback: Readback
    segment_name: SegmentName | None = None
    segment_description: str | None = None
    assumptions: list[Note]
    blocking_questions: list[Note]
    unsupported: list[Unsupported]

    @model_validator(mode="before")
    @classmethod
    def _one_note_as_a_list(cls, v: Any) -> Any:
        """One note sent as a plain string instead of a list of them (live 2026-10-26): a list of that one."""
        if isinstance(v, dict):
            v = {
                k: [x] if k in ("assumptions", "blocking_questions") and isinstance(x, str) else x for k, x in v.items()
            }
        return v


@dataclass
class Timing:
    """Where a run's time went: the model, each tool, the input the model wrote per tool, and each turn."""

    model_s: float = 0.0
    tools_s: dict[str, float] = field(default_factory=dict)
    input_chars: dict[str, int] = field(default_factory=dict)
    turns: list[dict[str, Any]] = field(default_factory=list)

    def tool(self, name: str, args: Any, seconds: float) -> None:
        """Adds one tool call: the size of its input and how long it ran (finish is judged later, not timed here)."""
        self.input_chars[name] = self.input_chars.get(name, 0) + len(json.dumps(args, default=str))
        if name != "finish":
            self.tools_s[name] = self.tools_s.get(name, 0) + seconds

    def as_dict(self) -> dict[str, Any]:
        """The diagnostics' timing entry."""
        return {
            "model_s": round(self.model_s, 2),
            "tools_s": {k: round(v, 2) for k, v in self.tools_s.items()},
            "input_chars": self.input_chars,
            "turns": self.turns,
        }


@dataclass
class Done:
    """A tool call as the graph's judge reads it (the artifact of its ToolMessage, never sent to the model).

    Attributes:
        input: The validated input, as the tools read it.
        result: The result as the model reads it; None for finish (judged after the turn).
    """

    input: ToolInput
    result: Result | None


@dataclass
class RunContext:
    """What one run's graph and tools share: the LangGraph run context.

    Attributes:
        llm: The chat model (model.ChatLLM, or a scripted one in tests).
        run: The run's tools as code.
        system: The system prompt blocks.
        parts: The parts of the offer the run builds.
        settings: The run's settings.
        timing: Where the run's time went.
        started: Tool calls started before the tools step, by tool call id.
        invalid: Ids of the tool calls whose input was not valid JSON.
        worker: Runs the calls started while the model streams, one at a time, in the order they were written.
        lookups: Runs a turn's lookups together (they only read the catalog).
        prompt: The manager's description (the test-case branch writes its cases from it).
        cases_llm: The chat model of the test-case branch (default: llm).
        plan: The offer's plan both the build run and the test cases read (plan.py), or None.
    """

    llm: Any
    run: Run
    system: list[dict[str, Any]]
    parts: tuple[str, ...]
    settings: Settings
    prompt: str = ""
    cases_llm: Any = None
    plan: dict[str, Any] | None = None
    timing: Timing = field(default_factory=Timing)
    started: dict[str, Future[ToolMessage]] = field(default_factory=dict)
    invalid: set[str] = field(default_factory=set)
    worker: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(1))
    lookups: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(4))

    def start(self, tool_call_id: str, name: str, args: dict[str, Any]) -> None:
        """Starts a tool call the model finished writing, while it writes the rest of its turn.

        Finish is judged after the turn; lookups run together once the turn is written.
        """
        if name in EARLY:
            self.started[tool_call_id] = self.worker.submit(run_now, self, tool_call_id, name, args)

    def start_lookups(self, calls: list[dict[str, Any]]) -> None:
        """Starts a turn's lookups together."""
        for c in calls:
            if c["name"] == "lookup_values" and c["id"] not in self.started and c["id"] not in self.invalid:
                self.started[c["id"]] = self.lookups.submit(run_now, self, c["id"], c["name"], c["args"])

    def close(self) -> None:
        """Waits for the calls still running, and stops the workers."""
        self.worker.shutdown(wait=True)
        self.lookups.shutdown(wait=True)


def _plain(value: Any) -> Any:
    """A validated value as the tools read it: models as dicts of the fields the model gave."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_unset=True)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _given(fields: dict[str, Any]) -> ToolInput:
    """A call's validated input as the tools read it: plain values, the fields the model left out absent."""
    return {k: _plain(v) for k, v in fields.items() if v is not None}


def content(result: Result) -> str:
    """A result as the tool message carries it to the model."""
    return json.dumps(result)[:MAX_RESULT_CHARS]


def _call(ctx: RunContext, method: Callable[[ToolInput], Result], fields: dict[str, Any]) -> tuple[str, Done]:
    """Runs one of the run's tools on a call's input; the result worded for the model.

    A failure on an input the schema allowed goes back to the model as an error and the run goes on.
    """
    x = _given(fields)
    try:
        out = render.reply(method(x))
    except Exception as e:  # the model's input broke the tool: it reads why and sends it again
        name = method.__name__
        log.exception("tool %s failed on the model's input", name)
        out = {
            "ok": False,
            "error": f"{name} could not read this input ({type(e).__name__}: {e}); check its shape "
            "against the tool schema and send it again",
        }
    return content(out), Done(x, out)


@tool("lookup_values", args_schema=LookupValues, response_format="content_and_artifact")
def lookup_values(runtime: ToolRuntime[RunContext], **fields: Any) -> tuple[str, Done]:
    """Find catalog codes for the manager's words, for an attribute whose values are not listed."""
    return _call(runtime.context, runtime.context.run.lookup_values, fields)


@tool("set_offer_main", args_schema=SetOfferMain, response_format="content_and_artifact")
def set_offer_main(runtime: ToolRuntime[RunContext], **fields: Any) -> tuple[str, Done]:
    """The Offer Main settings, and the prompt each later screen shows."""
    return _call(runtime.context, runtime.context.run.set_offer_main, fields)


@tool("compose", args_schema=Canvas, response_format="content_and_artifact")
def compose(runtime: ToolRuntime[RunContext], **fields: Any) -> tuple[str, Done]:
    """Write the canvas as XML and check it: block types and where each plugs in, field values and catalog codes, the fields the screen needs filled, and the counters. Returns the problems by block id and where they sit, and the loose blocks."""  # noqa: E501
    return _call(runtime.context, runtime.context.run.compose, fields)


@tool("finish", args_schema=Finish, response_format="content_and_artifact")
def finish(runtime: ToolRuntime[RunContext], **fields: Any) -> tuple[str, Done]:
    """Ends the run. Delivers the last clean compose of each canvas, with what the manager reads."""
    return "", Done(_given(fields), None)  # judged once the turn's other calls have run (graph.py)


TOOLS = [lookup_values, set_offer_main, compose, finish]
BY_NAME = {t.name: t for t in TOOLS}
EARLY = {"set_offer_main", "compose"}  # change the run's state: started while streaming, in call order


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """A Pydantic model's JSON schema as the model is shown it.

    Refs inlined, no titles or model docstrings, an optional field as its own type (not "X or null"), no null
    defaults, no `additionalProperties: true`.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def dropped(s: dict[str, Any], k: str, v: Any) -> bool:
        return (
            (k == "title" and isinstance(v, str))
            or (k == "default" and v is None)
            or (k == "additionalProperties" and v is True)
            or (k == "description" and s is raw)
        )

    def clean(s: Any) -> Any:
        if isinstance(s, list):
            return [clean(v) for v in s]
        if not isinstance(s, dict):
            return s
        if "$ref" in s:
            shared = {k: v for k, v in defs[s["$ref"].split("/")[-1]].items() if k != "description"}  # a docstring
            s = {**shared, **{k: v for k, v in s.items() if k != "$ref"}}
        branches = [b for b in s.get("anyOf", []) if b != {"type": "null"}]
        if "anyOf" in s and len(branches) == 1:  # an optional field: its one type, a model's ref inlined too
            return clean({**{k: v for k, v in s.items() if k != "anyOf"}, **branches[0]})
        if "anyOf" in s:
            s = dict(s, anyOf=branches)
        return {k: v if k == "required" else clean(v) for k, v in s.items() if not dropped(s, k, v)}

    return clean(raw)


def schemas() -> list[dict[str, Any]]:
    """The tools as the chat model is given them (name, description, input schema), built from the tools themselves."""
    return [
        {"name": t.name, "description": t.description, "input_schema": _schema(t.args_schema)}  # type: ignore[arg-type]
        for t in TOOLS
    ]


SCHEMAS = schemas()


def schema_error(error: ToolInvocationError) -> str:
    """A call whose input does not fit its tool's schema, as the model reads it.

    The first few fields that did not match, and what to do (ToolNode's handle_tool_errors; any other error is
    raised on).
    """
    if not isinstance(error, ToolInvocationError):
        raise error
    errs = error.filtered_errors if error.filtered_errors is not None else error.source.errors()
    return _schema_error(error.tool_name, errs)


def _schema_error(name: str, errors: Any) -> str:
    """The error result of a call whose input does not fit its schema."""
    errs = [f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in list(errors)[:6]]
    text = f"{name} input does not match its schema ({'; '.join(errs)}); fix these and send it again"
    return content({"ok": False, "error": text})


def _error_message(call_id: str, name: str, text: str) -> ToolMessage:
    """A tool message for a call that did not run."""
    return ToolMessage(content=text, tool_call_id=call_id, name=name, status="error")


def not_json(call_id: str, name: str) -> ToolMessage:
    """The result of a call whose input was not valid JSON: its tool's schema error for no input at all."""
    try:
        BY_NAME[name].args_schema.model_validate(None)  # type: ignore[union-attr]
    except ValidationError as e:
        return _error_message(call_id, name, _schema_error(name, e.errors()))
    return _error_message(call_id, name, content({"ok": False, "error": f"{name} input is not valid JSON"}))


def run_now(ctx: RunContext, call_id: str, name: str, args: dict[str, Any] | None) -> ToolMessage:
    """Runs one tool call outside the tools step (started while the turn streams, or a lookup), timed."""
    t0 = time.time()
    runtime = ToolRuntime(
        state=None, context=ctx, config={}, stream_writer=lambda _: None, tool_call_id=call_id, store=None
    )
    try:
        return BY_NAME[name].invoke(
            {"type": "tool_call", "id": call_id, "name": name, "args": {**(args or {}), "runtime": runtime}}
        )
    except ValidationError as e:
        return _error_message(call_id, name, _schema_error(name, e.errors(include_url=False)))
    finally:
        ctx.timing.tool(name, args, time.time() - t0)


def started_or_run(request: ToolCallRequest, execute: Callable[[ToolCallRequest], Any]) -> Any:
    """ToolNode's wrap_tool_call: a call already started is waited for, the others run here, timed.

    A call whose input was not valid JSON gets its schema error, an unknown tool says so.
    """
    ctx = cast(RunContext, request.runtime.context)
    call = request.tool_call
    call_id, name = call["id"] or "", call["name"]
    if call_id in ctx.invalid:
        ctx.timing.tool(name, None, 0.0)
        return not_json(call_id, name)
    if request.tool is None:
        return _error_message(call_id, name, content({"ok": False, "error": f"unknown tool {name}"}))
    if (future := ctx.started.pop(call_id, None)) is not None:
        return future.result()
    t0 = time.time()
    try:
        return execute(request)
    finally:
        ctx.timing.tool(name, call["args"], time.time() - t0)


def done(message: ToolMessage) -> tuple[ToolInput, Result | None]:
    """A tool message's validated input and result, as the judge reads them.

    A call that did not validate has no input and its error as its result.
    """
    if isinstance(message.artifact, Done):
        return message.artifact.input, message.artifact.result
    try:
        out = json.loads(message.content)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}, {"error": message.content}
    return {}, out if isinstance(out, dict) else {"result": out}
