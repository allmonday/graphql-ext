"""Tests for parser, resolver, and the full pipeline.

Covers:
  1. Minimal query parsing (no annotations)
  2. post annotation
  3. sendto annotation
  4. expose annotation (ancestor→descendant context)
  5. Field arguments
  6. Async resolver support
  7. Combined annotations
  8. Error cases
"""

import pytest
from graphql_ext import parse, resolve, Registry, ParseError, FieldNode, QueryDocument


# ============================================================================
# parser tests
# ============================================================================

class TestParser:
    async def test_single_field(self):
        doc = parse("{ hero { name } }")
        assert doc.fields["hero"].name == "hero"
        assert doc.fields["hero"].children["name"].name == "name"
        assert doc.fields["hero"].children["name"].children == {}

    async def test_two_top_level_fields(self):
        doc = parse("{ hero { name } villain { name } }")
        assert set(doc.fields.keys()) == {"hero", "villain"}

    async def test_nested_three_levels(self):
        doc = parse("{ hero { friends { name __typename } } }")
        friends = doc.fields["hero"].children["friends"]
        assert set(friends.children.keys()) == {"name", "__typename"}

    async def test_no_children_leaf(self):
        doc = parse("{ hero }")
        assert doc.fields["hero"].children == {}

    async def test_tolerates_query_keyword(self):
        doc = parse("query { hero { name } }")
        assert "hero" in doc.fields

    async def test_tolerates_mutation_keyword(self):
        doc = parse("mutation { hero { name } }")
        assert "hero" in doc.fields

    async def test_whitespace_insensitive(self):
        a = parse("{hero{name}}")
        b = parse("{\n  hero {\n    name\n  }\n}")
        assert a.fields["hero"].name == b.fields["hero"].name

    # --- post ---

    async def test_post_annotation(self):
        doc = parse("{ hero post trim_name { name } }")
        node = doc.fields["hero"]
        assert node.post == "trim_name"

    async def test_post_on_leaf(self):
        doc = parse("{ title post strip }")
        assert doc.fields["title"].post == "strip"

    # --- sendto ---

    async def test_sendto_annotation(self):
        doc = parse("{ hero { id sendto hero_id name } }")
        node = doc.fields["hero"].children["id"]
        assert node.sendto == "hero_id"

    # --- expose ---

    async def test_expose_annotation(self):
        doc = parse("{ hero { name expose hero_name friends { greeting } } }")
        node = doc.fields["hero"].children["name"]
        assert node.expose == "hero_name"

    # --- combined ---

    async def test_combined_annotations(self):
        doc = parse("{ hero post trim_name expose hero_ctx { id sendto hero_id name } }")
        hero = doc.fields["hero"]
        assert hero.post == "trim_name"
        assert hero.expose == "hero_ctx"
        assert hero.children["id"].sendto == "hero_id"

    async def test_annotation_order_independent(self):
        a = parse("{ hero post trim expose hero_ctx { name } }")
        b = parse("{ hero expose hero_ctx post trim { name } }")
        assert a.fields["hero"].post == b.fields["hero"].post == "trim"
        assert a.fields["hero"].expose == b.fields["hero"].expose == "hero_ctx"

    async def test_field_before_and_after_annotation(self):
        doc = parse("{ hero { id post int2str name expose story_name } }")
        id_node = doc.fields["hero"].children["id"]
        name_node = doc.fields["hero"].children["name"]
        assert id_node.post == "int2str"
        assert name_node.expose == "story_name"

    # --- errors ---

    def test_unclosed_brace(self):
        with pytest.raises(ParseError, match="expected '}'"):
            parse("{ hero { name ")

    def test_duplicate_field(self):
        with pytest.raises(ParseError, match="duplicate field"):
            parse("{ hero { name name } }")

    # --- edge cases ---

    async def test_empty_input(self):
        doc = parse("{}")
        assert doc.fields == {}

    async def test_multiple_top_level_fields(self):
        doc = parse("{ a { x } b { y } }")
        assert "a" in doc.fields
        assert "b" in doc.fields


# ============================================================================
# argument parsing tests
# ============================================================================

class TestArgParsing:
    async def test_single_int_arg(self):
        doc = parse("{ hero(id: 5) { name } }")
        assert doc.fields["hero"].args == {"id": 5}

    async def test_multiple_args(self):
        doc = parse('{ hero(id: 5 name: "alice") { name } }')
        assert doc.fields["hero"].args == {"id": 5, "name": "alice"}

    async def test_string_arg(self):
        doc = parse('{ search(query: "hello world") }')
        assert doc.fields["search"].args == {"query": "hello world"}

    async def test_float_arg(self):
        doc = parse("{ item(price: 3.14) }")
        assert doc.fields["item"].args == {"price": 3.14}

    async def test_bool_arg(self):
        doc = parse("{ items(active: true) }")
        assert doc.fields["items"].args == {"active": True}

    async def test_null_arg(self):
        doc = parse("{ items(filter: null) }")
        assert doc.fields["items"].args == {"filter": None}

    async def test_enum_like_arg(self):
        doc = parse("{ items(sort: ASCENDING) }")
        assert doc.fields["items"].args == {"sort": "ASCENDING"}

    async def test_negative_int(self):
        doc = parse("{ items(offset: -1) }")
        assert doc.fields["items"].args == {"offset": -1}

    async def test_negative_float(self):
        doc = parse("{ items(factor: -0.5) }")
        assert doc.fields["items"].args == {"factor": -0.5}

    async def test_empty_args(self):
        doc = parse("{ hero() { name } }")
        assert doc.fields["hero"].args == {}

    async def test_args_with_annotations(self):
        doc = parse('{ hero(id: 5) post trim { name } }')
        assert doc.fields["hero"].args == {"id": 5}
        assert doc.fields["hero"].post == "trim"

    async def test_args_on_nested_field(self):
        doc = parse("{ hero { friends(limit: 10) { name } } }")
        assert doc.fields["hero"].children["friends"].args == {"limit": 10}

    async def test_string_escape(self):
        doc = parse(r'{ hero(name: "say \"hi\"") }')
        assert doc.fields["hero"].args == {"name": 'say "hi"'}

    def test_missing_colon_in_arg(self):
        with pytest.raises(ParseError, match="expected ':'"):
            parse("{ hero(id 5) }")

    def test_unclosed_paren(self):
        with pytest.raises(ParseError):
            parse("{ hero(id: 5 }")


# ============================================================================
# resolver tests
# ============================================================================

class TestResolver:
    async def test_basic_resolve(self):
        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {"name": "bob"},
            }
        )
        doc = parse("{ hero { name } }")
        result = await resolve(doc, registry)
        assert result == {"hero": {"name": "bob"}}

    async def test_fallback_attribute_access(self):
        class Hero:
            def __init__(self):
                self.name = "alice"

        registry = Registry()
        doc = parse("{ hero { name } }")
        result = await resolve(doc, registry, root={"hero": Hero()})
        assert result["hero"]["name"] == "alice"

    # --- post hook ---

    async def test_post_hook_called(self):
        def _trim(v, **ctx):
            return v.strip()

        registry = Registry(
            resolvers={
                "hero.name": lambda parent, **ctx: " bob ",
            },
            post_hooks={
                "trim": _trim,
            },
        )
        doc = parse("{ hero { name post trim } }")
        result = await resolve(doc, registry, root={"hero": {}})
        assert result["hero"]["name"] == "bob"

    async def test_post_hook_unknown_passthrough(self):
        registry = Registry(
            resolvers={"title": lambda parent, **ctx: "hello"},
        )
        doc = parse("{ title post none_such }")
        result = await resolve(doc, registry)
        assert result["title"] == "hello"

    # --- sendto ---

    async def test_sendto_passes_to_sibling_resolver(self):
        def _resolve_name(parent, **ctx):
            greeting = ctx.get("greet_prefix", "")
            return f"{greeting}{parent['name']}"

        registry = Registry(
            resolvers={
                "hero.prefix": lambda parent, **ctx: "Ms. ",
                "hero.name": _resolve_name,
            },
        )
        doc = parse("{ hero { prefix sendto greet_prefix name } }")
        result = await resolve(doc, registry, root={"hero": {"prefix": "", "name": "alice"}})
        assert "Ms. alice" in str(result)

    # --- expose (ancestor→descendant context) ---

    async def test_expose_makes_value_available_to_descendants(self):
        def _resolve_greeting(parent, **ctx):
            hero_name = ctx.get("ancestor_context", {}).get("hero_name", "unknown")
            return f"Hello from {hero_name}"

        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {"name": "Luke", "friends": [{"id": 1}]},
                "hero.name": lambda parent, **ctx: parent["name"],
                "hero.friends": lambda parent, **ctx: parent["friends"],
                "hero.friends.greeting": _resolve_greeting,
            },
        )
        doc = parse("{ hero { name expose hero_name friends { greeting } } }")
        result = await resolve(doc, registry)
        assert result["hero"]["friends"][0]["greeting"] == "Hello from Luke"

    async def test_expose_with_post_hook(self):
        def _trim(v, **ctx):
            return v.strip()

        def _resolve_desc(parent, **ctx):
            hero_name = ctx.get("ancestor_context", {}).get("hero_name", "unknown")
            return f"desc sees: {hero_name}"

        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {"name": "  Luke  ", "child": {}},
                "hero.name": lambda parent, **ctx: parent["name"],
                "hero.child": lambda parent, **ctx: parent["child"],
                "hero.child.desc": _resolve_desc,
            },
            post_hooks={"trim": _trim},
        )
        doc = parse("{ hero { name post trim expose hero_name child { desc } } }")
        result = await resolve(doc, registry)
        assert result["hero"]["child"]["desc"] == "desc sees: Luke"

    async def test_expose_available_in_post_hook(self):
        def _add_env(v, **ctx):
            env = ctx.get("ancestor_context", {}).get("env_name", "dev")
            return f"{v} ({env})"

        registry = Registry(
            resolvers={
                "config": lambda parent, **ctx: {"env": "production", "items": [{"label": "item1"}]},
                "config.env": lambda parent, **ctx: parent["env"],
                "config.items": lambda parent, **ctx: parent["items"],
                "config.items.label": lambda parent, **ctx: parent["label"],
            },
            post_hooks={"add_env": _add_env},
        )
        doc = parse("{ config { env expose env_name items { label post add_env } } }")
        result = await resolve(doc, registry)
        assert result["config"]["items"][0]["label"] == "item1 (production)"

    async def test_expose_nested_depth(self):
        def _resolve_deep(parent, **ctx):
            a_x = ctx.get("ancestor_context", {}).get("A_X", "missing")
            return a_x

        registry = Registry(
            resolvers={
                "a": lambda parent, **ctx: {"x": "hello", "b": {"c": {}}},
                "a.x": lambda parent, **ctx: parent["x"],
                "a.b": lambda parent, **ctx: parent["b"],
                "a.b.c": lambda parent, **ctx: parent["c"],
                "a.b.c.y": _resolve_deep,
            },
        )
        doc = parse("{ a { x expose A_X b { c { y } } } }")
        result = await resolve(doc, registry)
        assert result["a"]["b"]["c"]["y"] == "hello"

    async def test_expose_with_sendto(self):
        """sendto only passes to siblings at the same level, not descendants.
        expose passes to descendants via ancestor_context.
        """
        def _resolve_full(parent, **ctx):
            hero_name = ctx.get("ancestor_context", {}).get("hero_name", "?")
            return f"name={hero_name}"

        def _resolve_label(parent, **ctx):
            hero_id = ctx.get("hero_id", "?")
            return f"id={hero_id}"

        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {"id": 42, "name": "Luke", "friends": [{"fid": 1}]},
                "hero.id": lambda parent, **ctx: parent["id"],
                "hero.name": lambda parent, **ctx: parent["name"],
                "hero.friends": lambda parent, **ctx: parent["friends"],
                "hero.friends.full": _resolve_full,
                "hero.label": _resolve_label,
            },
        )
        doc = parse("{ hero { id sendto hero_id name expose hero_name label friends { full } } }")
        result = await resolve(doc, registry)
        assert result["hero"]["label"] == "id=42"
        assert result["hero"]["friends"][0]["full"] == "name=Luke"

    async def test_multiple_expose_at_different_levels(self):
        def _resolve_z(parent, **ctx):
            ac = ctx.get("ancestor_context", {})
            return f"A_X={ac.get('A_X', '?')}, B_Y={ac.get('B_Y', '?')}"

        registry = Registry(
            resolvers={
                "a": lambda parent, **ctx: {"x": "hello", "b": {"y": "world", "c": {}}},
                "a.x": lambda parent, **ctx: parent["x"],
                "a.b": lambda parent, **ctx: parent["b"],
                "a.b.y": lambda parent, **ctx: parent["y"],
                "a.b.c": lambda parent, **ctx: parent["c"],
                "a.b.c.z": _resolve_z,
            },
        )
        doc = parse("{ a { x expose A_X b { y expose B_Y c { z } } } }")
        result = await resolve(doc, registry)
        assert result["a"]["b"]["c"]["z"] == "A_X=hello, B_Y=world"


# ============================================================================
# argument resolving tests
# ============================================================================

class TestArgResolving:
    async def test_args_passed_to_resolver(self):
        def _resolve_hero(parent, **ctx):
            hero_id = ctx["args"]["id"]
            return {"name": f"hero-{hero_id}"}

        registry = Registry(resolvers={"hero": _resolve_hero})
        doc = parse("{ hero(id: 5) { name } }")
        result = await resolve(doc, registry)
        assert result["hero"]["name"] == "hero-5"

    async def test_args_passed_to_post_hook(self):
        def _format_name(value, **ctx):
            style = ctx["args"].get("style", "plain")
            if style == "upper":
                return value.upper()
            return value

        registry = Registry(
            resolvers={"hero.name": lambda parent, **ctx: "alice"},
            post_hooks={"format": _format_name},
        )
        doc = parse('{ hero { name(style: "upper") post format } }')
        result = await resolve(doc, registry)
        assert result["hero"]["name"] == "ALICE"

    async def test_args_empty_when_not_provided(self):
        received_args = {}
        def _resolve_hero(parent, **ctx):
            received_args.update(ctx.get("args", {}))
            return {"name": "bob"}

        registry = Registry(resolvers={"hero": _resolve_hero})
        doc = parse("{ hero { name } }")
        await resolve(doc, registry)
        assert received_args == {}


# ============================================================================
# async resolver tests
# ============================================================================

class TestAsyncResolving:
    async def test_async_resolver(self):
        async def _resolve_hero(parent, **ctx):
            return {"name": "bob"}

        registry = Registry(resolvers={"hero": _resolve_hero})
        doc = parse("{ hero { name } }")
        result = await resolve(doc, registry)
        assert result == {"hero": {"name": "bob"}}

    async def test_async_post_hook(self):
        async def _trim(value, **ctx):
            return value.strip()

        registry = Registry(
            resolvers={"hero.name": lambda parent, **ctx: " bob "},
            post_hooks={"trim": _trim},
        )
        doc = parse("{ hero { name post trim } }")
        result = await resolve(doc, registry, root={"hero": {}})
        assert result["hero"]["name"] == "bob"

    async def test_mixed_sync_and_async_resolvers(self):
        async def _resolve_hero(parent, **ctx):
            return {"name": "alice", "friends": [{"name": "bob"}]}

        def _resolve_friends(parent, **ctx):
            return parent["friends"]

        async def _resolve_name(parent, **ctx):
            return parent["name"].upper()

        registry = Registry(
            resolvers={
                "hero": _resolve_hero,
                "hero.friends": _resolve_friends,
                "hero.friends.name": _resolve_name,
            },
        )
        doc = parse("{ hero { friends { name } } }")
        result = await resolve(doc, registry)
        assert result["hero"]["friends"][0]["name"] == "BOB"

    async def test_async_resolver_with_args(self):
        async def _resolve_hero(parent, **ctx):
            hero_id = ctx["args"]["id"]
            return {"name": f"hero-{hero_id}"}

        registry = Registry(resolvers={"hero": _resolve_hero})
        doc = parse("{ hero(id: 42) { name } }")
        result = await resolve(doc, registry)
        assert result["hero"]["name"] == "hero-42"

    async def test_async_fallback_still_works(self):
        registry = Registry()
        doc = parse("{ hero { name } }")
        result = await resolve(doc, registry, root={"hero": {"name": "alice"}})
        assert result["hero"]["name"] == "alice"


# ============================================================================
# round-trip pipeline
# ============================================================================

class TestPipeline:
    async def test_hero_query_full(self):
        def _resolve_hero(parent, **ctx):
            return {
                "name": "  Luke  ",
                "friends": [
                    {"name": "Leia", "rank": "general"},
                    {"name": "Han", "rank": "captain"},
                ],
            }

        def _resolve_friends(parent, **ctx):
            return parent["friends"]

        def _resolve_rank(parent, **ctx):
            return parent.get("rank", "")

        def _trim(value, **ctx):
            return value.strip()

        registry = Registry(
            resolvers={
                "hero": _resolve_hero,
                "hero.friends": _resolve_friends,
                "hero.friends.rank": _resolve_rank,
            },
            post_hooks={
                "trim": _trim,
            },
        )
        query = """
        {
            hero {
                name post trim expose hero_name
                friends {
                    name
                    rank expose rank_info
                }
                __typename
            }
        }
        """
        doc = parse(query)
        result = await resolve(doc, registry)
        assert result["hero"]["name"] == "Luke"

    async def test_keyword_tolerated(self):
        registry = Registry(
            resolvers={"x": lambda parent, **ctx: 1}
        )
        for kw in ("query", "mutation"):
            doc = parse(f"{kw} {{ x }}")
            result = await resolve(doc, registry)
            assert result["x"] == 1
