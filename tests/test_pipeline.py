"""Tests for parser, resolver, and the full pipeline.

Covers:
  1. Minimal query parsing (no annotations)
  2. post annotation
  3. sendto annotation
  4. expose annotation
  5. Combined annotations
  6. Error cases
"""

import pytest
from graphql_ext import parse, resolve, Registry, ParseError, FieldNode, QueryDocument


# ============================================================================
# parser tests
# ============================================================================

class TestParser:
    def test_single_field(self):
        doc = parse("{ hero { name } }")
        assert doc.fields["hero"].name == "hero"
        assert doc.fields["hero"].children["name"].name == "name"
        assert doc.fields["hero"].children["name"].children == {}

    def test_two_top_level_fields(self):
        doc = parse("{ hero { name } villain { name } }")
        assert set(doc.fields.keys()) == {"hero", "villain"}

    def test_nested_three_levels(self):
        doc = parse("{ hero { friends { name __typename } } }")
        friends = doc.fields["hero"].children["friends"]
        assert set(friends.children.keys()) == {"name", "__typename"}

    def test_no_children_leaf(self):
        doc = parse("{ hero }")
        assert doc.fields["hero"].children == {}

    def test_tolerates_query_keyword(self):
        doc = parse("query { hero { name } }")
        assert "hero" in doc.fields

    def test_tolerates_mutation_keyword(self):
        doc = parse("mutation { hero { name } }")
        assert "hero" in doc.fields

    def test_whitespace_insensitive(self):
        a = parse("{hero{name}}")
        b = parse("{\n  hero {\n    name\n  }\n}")
        assert a.fields["hero"].name == b.fields["hero"].name

    # --- post ---

    def test_post_annotation(self):
        doc = parse("{ hero post trim_name { name } }")
        node = doc.fields["hero"]
        assert node.post == "trim_name"

    def test_post_on_leaf(self):
        doc = parse("{ title post strip }")
        assert doc.fields["title"].post == "strip"

    # --- sendto ---

    def test_sendto_annotation(self):
        doc = parse("{ hero { id sendto hero_id name } }")
        node = doc.fields["hero"].children["id"]
        assert node.sendto == "hero_id"

    # --- expose ---

    def test_expose_annotation(self):
        doc = parse("{ hero { secret expose api name } }")
        node = doc.fields["hero"].children["secret"]
        assert node.expose == "api"

    # --- combined ---

    def test_combined_annotations(self):
        doc = parse("{ hero post trim_name expose api { id sendto hero_id name } }")
        hero = doc.fields["hero"]
        assert hero.post == "trim_name"
        assert hero.expose == "api"
        assert hero.children["id"].sendto == "hero_id"

    def test_annotation_order_independent(self):
        a = parse("{ hero post trim expose api { name } }")
        b = parse("{ hero expose api post trim { name } }")
        assert a.fields["hero"].post == b.fields["hero"].post == "trim"
        assert a.fields["hero"].expose == b.fields["hero"].expose == "api"

    def test_field_before_and_after_annotation(self):
        doc = parse("{ hero { id post int2str name expose mcp } }")
        id_node = doc.fields["hero"].children["id"]
        name_node = doc.fields["hero"].children["name"]
        assert id_node.post == "int2str"
        assert name_node.expose == "mcp"

    # --- errors ---

    def test_unclosed_brace(self):
        with pytest.raises(ParseError, match="expected '}'"):
            parse("{ hero { name ")

    def test_duplicate_field(self):
        with pytest.raises(ParseError, match="duplicate field"):
            parse("{ hero { name name } }")

    # --- edge cases ---

    def test_empty_input(self):
        doc = parse("{}")
        assert doc.fields == {}

    def test_multiple_top_level_fields(self):
        doc = parse("{ a { x } b { y } }")
        assert "a" in doc.fields
        assert "b" in doc.fields


# ============================================================================
# resolver tests
# ============================================================================

class TestResolver:
    def test_basic_resolve(self):
        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {"name": "bob"},
            }
        )
        doc = parse("{ hero { name } }")
        result = resolve(doc, registry)
        assert result == {"hero": {"name": "bob"}}

    def test_fallback_attribute_access(self):
        """When no resolver registered, fallback to dict key access."""
        class Hero:
            def __init__(self):
                self.name = "alice"

        registry = Registry()
        doc = parse("{ hero { name } }")
        result = resolve(doc, registry, root={"hero": Hero()})
        assert result["hero"]["name"] == "alice"

    # --- post hook ---

    def test_post_hook_called(self):
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
        result = resolve(doc, registry, root={"hero": {}})
        assert result["hero"]["name"] == "bob"

    def test_post_hook_unknown_passthrough(self):
        registry = Registry(
            resolvers={"title": lambda parent, **ctx: "hello"},
        )
        doc = parse("{ title post none_such }")
        result = resolve(doc, registry)
        assert result["title"] == "hello"

    # --- sendto ---

    def test_sendto_passes_to_sibling_resolver(self):
        """sendto value should be available to subsequent resolvers."""
        def _resolve_name(parent, **ctx):
            # ctx contains sendto values from prior siblings
            greeting = ctx.get("greet_prefix", "")
            return f"{greeting}{parent['name']}"

        registry = Registry(
            resolvers={
                "hero.prefix": lambda parent, **ctx: "Ms. ",
                "hero.name": _resolve_name,
            },
        )
        doc = parse("{ hero { prefix sendto greet_prefix name } }")
        result = resolve(doc, registry, root={"hero": {"prefix": "", "name": "alice"}})
        assert "Ms. alice" in str(result)

    # --- expose ---

    def test_expose_buttoned_field_not_in_output(self):
        registry = Registry(
            resolvers={
                "hero": lambda parent, **ctx: {
                    "public_name": "bob",
                    "secret": "ssn1234",
                }
            }
        )
        doc = parse("{ hero { public_name secret expose api } }")
        result = resolve(doc, registry, protocol="mcp")
        # secret should be filtered out for mcp protocol
        assert "public_name" in result["hero"]
        assert "secret" not in result["hero"]


# ============================================================================
# round-trip pipeline
# ============================================================================

class TestPipeline:
    def test_hero_query_full(self):
        """Realistic query with all three annotations."""
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
            hero expose api {
                name post trim
                friends {
                    name
                    rank expose api
                }
                __typename
            }
        }
        """
        doc = parse(query)
        result = resolve(doc, registry, protocol="api")
        assert result["hero"]["name"] == "Luke"

    def test_keyword_tolerated(self):
        registry = Registry(
            resolvers={"x": lambda parent, **ctx: 1}
        )
        for kw in ("query", "mutation"):
            doc = parse(f"{kw} {{ x }}")
            result = resolve(doc, registry)
            assert result["x"] == 1
