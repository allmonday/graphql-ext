"""Execution engine for the extended GraphQL DSL.

Drives the annotation pipeline:
  1. Load data via user-provided resolver functions.
  2. sendto: pass data to children's context (per-field annotation).
  3. post: post-process field value.
  4. expose: filter output by protocol.

The resolver itself is kept minimal — all business logic is
supplied through a Registry of resolve/post hooks.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from .ast import FieldNode, QueryDocument


# ---------------------------------------------------------------------------
# registry of user-provided resolver / post hooks
# ---------------------------------------------------------------------------

@dataclass
class Registry:
    """Maps field paths → resolver functions and post-hook names → callables."""

    # field resolvers: "hero" / "hero.friends" → callable
    resolvers: dict[str, Callable] = field(default_factory=dict)

    # post hooks: "trim_name" / "resolve_friends" → callable
    post_hooks: dict[str, Callable] = field(default_factory=dict)

    def resolve_field(self, path: str, parent: Any = None, **ctx) -> Any:
        fn = self.resolvers.get(path)
        if fn is not None:
            return fn(parent, **ctx)
        # fallback: try attribute access on parent dict or object
        if parent is None:
            return None
        field_name = path.split(".")[-1]
        if isinstance(parent, dict):
            return parent.get(field_name)
        return getattr(parent, field_name, None)

    def run_post(self, name: str, value: Any, **ctx) -> Any:
        fn = self.post_hooks.get(name)
        if fn is not None:
            return fn(value, **ctx)
        return value  # unknown hook → pass-through


# ---------------------------------------------------------------------------
# resolver engine
# ---------------------------------------------------------------------------

def resolve(
    doc: QueryDocument,
    registry: Registry,
    root: Any = None,
    protocol: str = "api",
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Walk the parsed QueryDocument, load data, and apply annotations.

    Parameters
    -----------
    doc : QueryDocument
        Parsed AST.
    registry : Registry
        User-supplied resolvers and post hooks.
    root : Any
        Root data object (e.g. a parent model instance).
    protocol : str
        Current output protocol (affects ``expose`` filtering).
    ctx : dict | None
        Additional context, e.g. parent data sent via ``sendto``.

    Returns
    -------
    dict[str, Any]
        The resolved result tree.
    """
    ctx = ctx or {}

    def _resolve_node(
        node: FieldNode,
        parent: Any,
        path_prefix: str,
        siblings_sendto: dict[str, Any] | None,
    ) -> Any:
        full_path = f"{path_prefix}.{node.name}" if path_prefix else node.name

        # --- 1. load ---
        value = registry.resolve_field(full_path, parent=parent, **(siblings_sendto or {}))

        # --- 2. recurse into children ---
        if node.children:
            # First pass: resolve sendto fields so their values are available to siblings
            child_ctx: dict[str, Any] = {}
            for child in node.children.values():
                if child.sendto:
                    # The sendto field's resolved value is what gets passed
                    child_ctx[child.sendto] = _resolve_node(
                        child, value, full_path, siblings_sendto=None
                    )

            # Second pass: resolve all children normally, with sendto context available
            if isinstance(value, list):
                # Array: resolve children for each element
                result_children: list[dict[str, Any]] = []
                for item in value:
                    item_result: dict[str, Any] = {}
                    for child in node.children.values():
                        item_result[child.name] = _resolve_node(
                            child, item, full_path, siblings_sendto=child_ctx,
                        )
                    result_children.append(item_result)
                value = result_children
            else:
                result_children: dict[str, Any] = {}
                for child in node.children.values():
                    result_children[child.name] = _resolve_node(
                        child, value, full_path, siblings_sendto=child_ctx,
                    )
                value = result_children

        # --- 3. post hook ---
        if node.post:
            value = registry.run_post(node.post, value)

        return value

    # Walk top-level fields
    raw: dict[str, Any] = {}
    for name, field_node in doc.fields.items():
        raw[name] = _resolve_node(field_node, root, "", siblings_sendto=None)

    # --- expose filtering ---
    return _filter_exposed(doc, raw, protocol)


def _filter_exposed(
    doc: QueryDocument,
    result: dict[str, Any],
    protocol: str,
) -> dict[str, Any]:
    """Remove fields marked expose=<other-protocol> from the result tree."""
    out: dict[str, Any] = {}
    for name, value in result.items():
        node = doc.fields.get(name)
        if node is None:
            out[name] = value
            continue

        if node.expose is not None and node.expose != protocol:
            continue  # skip this field for this protocol

        # recurse into children if the value is a dict and has children in the AST
        if node.children and isinstance(value, dict):
            out[name] = _filter_children(node, value, protocol)
        elif node.children and isinstance(value, list):
            # array of objects
            out[name] = [
                _filter_children(node, item, protocol) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[name] = value

    return out


def _filter_children(
    node: FieldNode,
    value: dict[str, Any],
    protocol: str,
) -> dict[str, Any]:
    """Filter child fields based on their expose annotations."""
    out: dict[str, Any] = {}
    for child_name, child_node in node.children.items():
        if child_node.expose is not None and child_node.expose != protocol:
            continue
        child_value = value.get(child_name)
        # recurse further
        if child_node.children and isinstance(child_value, dict):
            out[child_name] = _filter_children(child_node, child_value, protocol)
        elif child_node.children and isinstance(child_value, list):
            out[child_name] = [
                _filter_children(child_node, item, protocol) if isinstance(item, dict) else item
                for item in child_value
            ]
        else:
            out[child_name] = child_value
    return out
