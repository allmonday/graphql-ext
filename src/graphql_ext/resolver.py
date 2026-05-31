"""Execution engine for the extended GraphQL DSL.

Drives the annotation pipeline:
  1. Load data via user-provided resolver functions.
  2. sendto: pass data to sibling resolvers in the same level.
  3. post: post-process field value.
  4. expose: expose field value to descendant resolvers via ancestor_context.

Supports both sync and async resolver / post-hook functions.
The main resolve() entry point is async.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from .ast import FieldNode, QueryDocument


# ---------------------------------------------------------------------------
# registry of user-provided resolver / post hooks
# ---------------------------------------------------------------------------

@dataclass
class Registry:
    """Maps field paths → resolver functions and post-hook names → callables."""

    resolvers: dict[str, Callable] = field(default_factory=dict)
    post_hooks: dict[str, Callable] = field(default_factory=dict)

    async def resolve_field(self, path: str, parent: Any = None, **ctx) -> Any:
        fn = self.resolvers.get(path)
        if fn is not None:
            if inspect.iscoroutinefunction(fn):
                return await fn(parent, **ctx)
            return fn(parent, **ctx)
        # fallback: try attribute access on parent dict or object
        if parent is None:
            return None
        field_name = path.split(".")[-1]
        if isinstance(parent, dict):
            return parent.get(field_name)
        return getattr(parent, field_name, None)

    async def run_post(self, name: str, value: Any, **ctx) -> Any:
        fn = self.post_hooks.get(name)
        if fn is not None:
            if inspect.iscoroutinefunction(fn):
                return await fn(value, **ctx)
            return fn(value, **ctx)
        return value


# ---------------------------------------------------------------------------
# resolver engine
# ---------------------------------------------------------------------------

async def resolve(
    doc: QueryDocument,
    registry: Registry,
    root: Any = None,
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Walk the parsed QueryDocument, load data, and apply annotations.

    Supports both sync and async resolver / post-hook functions.
    """
    ctx = ctx or {}

    async def _resolve_node(
        node: FieldNode,
        parent: Any,
        path_prefix: str,
        siblings_sendto: dict[str, Any] | None,
        ancestor_context: dict[str, Any] | None,
    ) -> Any:
        full_path = f"{path_prefix}.{node.name}" if path_prefix else node.name

        # --- 1. load ---
        value = await registry.resolve_field(
            full_path, parent=parent,
            args=node.args,
            **(siblings_sendto or {}),
            ancestor_context=ancestor_context or {},
        )

        # --- 2. recurse into children ---
        if node.children:
            child_ctx: dict[str, Any] = {}
            for child in node.children.values():
                if child.sendto:
                    child_ctx[child.sendto] = await _resolve_node(
                        child, value, full_path,
                        siblings_sendto=None,
                        ancestor_context=ancestor_context,
                    )

            if isinstance(value, list):
                result_children: list[dict[str, Any]] = []
                for item in value:
                    item_result: dict[str, Any] = {}
                    for child in node.children.values():
                        item_result[child.name] = await _resolve_node(
                            child, item, full_path,
                            siblings_sendto=child_ctx,
                            ancestor_context=ancestor_context,
                        )
                    result_children.append(item_result)
                value = result_children
            else:
                result_children: dict[str, Any] = {}
                for child in node.children.values():
                    result_children[child.name] = await _resolve_node(
                        child, value, full_path,
                        siblings_sendto=child_ctx,
                        ancestor_context=ancestor_context,
                    )
                value = result_children

        # --- 3. post hook ---
        if node.post:
            value = await registry.run_post(
                node.post, value,
                args=node.args,
                ancestor_context=ancestor_context or {},
            )

        # --- 4. expose: store value into ancestor_context for descendants ---
        if node.expose:
            if ancestor_context is None:
                ancestor_context = {}
            ancestor_context[node.expose] = value

        return value

    raw: dict[str, Any] = {}
    for name, field_node in doc.fields.items():
        raw[name] = await _resolve_node(
            field_node, root, "",
            siblings_sendto=None,
            ancestor_context={},
        )

    return raw
