"""Minimal AST for extended GraphQL DSL.

No fragment, no alias, no variable definitions.
Field-level annotations: post / sendto / expose (ancestor→descendant context).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldNode:
    name: str
    post: str | None = None       # registered resolver function name
    sendto: str | None = None     # context key to pass to children
    expose: str | None = None     # context key to expose to descendants via ancestor_context
    args: dict[str, Any] = field(default_factory=dict)
    children: dict[str, "FieldNode"] = field(default_factory=dict)

    def __repr__(self):
        parts = [self.name]
        if self.args:
            parts.append(f"args={self.args}")
        if self.post:
            parts.append(f"post={self.post}")
        if self.sendto:
            parts.append(f"sendto={self.sendto}")
        if self.expose:
            parts.append(f"expose={self.expose}")
        return "FieldNode(" + " ".join(parts) + ")"


@dataclass
class QueryDocument:
    """Root of a parsed query."""
    fields: dict[str, FieldNode] = field(default_factory=dict)

    def __repr__(self):
        return f"QueryDocument({list(self.fields.keys())})"
