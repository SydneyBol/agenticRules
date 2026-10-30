"""Problems found while building, checking or running an offer, as data.

Code that checks a canvas or a finish reports a `Problem`: a stable code, the block it is about and the
facts behind it. It never words them. What the model reads is written from them in `offer_author.ai.render`, what
the manager reads in `offer_author.api.text`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

BLOCK_REF = re.compile(r"^([\w-]+)#([\w-]+)$")


@dataclass(frozen=True)
class BlockRef:
    """A block of a canvas, as problems name it.

    Attributes:
        type: The block type, e.g. ``action``.
        id: The block id the model knows, e.g. ``b9``.
        path: Where the block sits, e.g. ``activity > rule 'X'.ACTION > action``; None until the caller locates it.
    """

    type: str
    id: str
    path: str | None = None

    @classmethod
    def parse(cls, text: str) -> BlockRef | None:
        """Reads a ``type#id`` reference as the stock screen reports it; None when the text is not one."""
        m = BLOCK_REF.match(text.strip())
        return cls(m[1], m[2]) if m else None


def _frozen(value: Any) -> Any:
    """A fact as a hashable value: lists and tuples as tuples, mappings as sorted item tuples."""
    if isinstance(value, list | tuple):
        return tuple(_frozen(v) for v in value)
    if isinstance(value, Mapping):
        return tuple(sorted((k, _frozen(v)) for k, v in value.items()))
    return value


@dataclass(frozen=True)
class Problem:
    """One thing to fix or report, without its wording.

    Attributes:
        code: What kind of problem it is; each code has one wording per audience.
        block: The block it is about, when there is one.
        facts: The values its wording needs, as sorted (name, value) pairs (hashable, so problems compare and dedupe).
    """

    code: str
    block: BlockRef | None = None
    facts: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def of(cls, code: str, block: BlockRef | None = None, **facts: Any) -> Problem:
        """A problem with its facts given by name."""
        return cls(code, block, tuple(sorted((k, _frozen(v)) for k, v in facts.items())))

    @property
    def data(self) -> dict[str, Any]:
        """The facts by name."""
        return dict(self.facts)

    def fact(self, name: str, default: Any = None) -> Any:
        """One fact, or default when the problem has none of that name."""
        return self.data.get(name, default)

    def located(self, paths: Mapping[str, str]) -> Problem:
        """The problem with its block's path filled in from paths (block id -> where it sits), when known."""
        if self.block is None or self.block.id not in paths:
            return self
        return replace(self, block=replace(self.block, path=paths[self.block.id]))

    def renamed(self, ids: Mapping[str, str]) -> Problem:
        """The problem with its block id mapped through ids (an id in the saved XML -> the id the model knows)."""
        if self.block is None or self.block.id not in ids:
            return self
        return replace(self, block=replace(self.block, id=ids[self.block.id]))


def located(problems: Iterable[Problem], paths: Mapping[str, str]) -> list[Problem]:
    """Every problem with its block's path filled in, where paths knows the block."""
    return [p.located(paths) for p in problems]


def renamed(problems: Iterable[Problem], ids: Mapping[str, str]) -> list[Problem]:
    """Every problem with its block id mapped back to the id the model knows."""
    return [p.renamed(ids) for p in problems]
