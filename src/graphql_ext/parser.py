"""Hand-rolled recursive-descent parser for the extended mini-GraphQL DSL.

Grammar (informal):
    query_doc := field+
    field     := NAME annotations? ('{' field* '}')?
    annotations := annotation+
    annotation := POST NAME
                | SENDTO NAME
                | EXPOSE NAME
    NAME      := [a-zA-Z_][a-zA-Z0-9_]*

No fragment spreads, no aliases, no variable definitions.
Whitespace is insignificant; curly braces delimit children.
"""

from .ast import FieldNode, QueryDocument


class ParseError(Exception):
    """Parse failure with position info."""
    def __init__(self, msg: str, pos: int, source: str):
        self.msg = msg
        self.pos = pos
        self.source = source
        # extract a line hint
        prefix = source[:pos]
        line = prefix.count("\n") + 1
        col = pos - (prefix.rfind("\n") + 1) + 1
        super().__init__(f"{msg} at line {line}, col {col}")


# ---------------------------------------------------------------------------
# built-in annotation keywords
# ---------------------------------------------------------------------------
_ANNOTATIONS = {"post", "sendto", "expose"}

# name character class
def _is_name_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"

def _is_name_cont(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class Parser:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.end = len(source)

    # ------------------------------------------------------------------ helpers

    def _peek(self) -> str:
        """Return current char or '' at EOF."""
        return self.src[self.pos] if self.pos < self.end else ""

    def _advance(self) -> str:
        """Consume and return current char."""
        ch = self._peek()
        self.pos += 1
        return ch

    def _skip_ws(self):
        while self.pos < self.end and self.src[self.pos] in " \t\n\r,":
            self.pos += 1

    def _error(self, msg: str):
        raise ParseError(msg, self.pos, self.src)

    # ---------------------------------------------------------------- grammar

    def parse_document(self) -> QueryDocument:
        self._skip_ws()

        # tolerate optional leading keyword "query" / "mutation" as a hint
        kw = None
        if self.src[self.pos:].startswith("query"):
            kw = "query"
        elif self.src[self.pos:].startswith("mutation"):
            kw = "mutation"
        if kw:
            self.pos += len(kw)
            self._skip_ws()

        # expect opening brace '{'
        if self._peek() == "{":
            self._advance()
            self._skip_ws()

        fields: dict[str, FieldNode] = {}
        while self.pos < self.end:
            self._skip_ws()
            ch = self._peek()
            if ch == "" or ch == "}":
                break
            node = self._parse_field()
            if node.name in fields:
                self._error(f"duplicate field '{node.name}'")
            fields[node.name] = node

        return QueryDocument(fields=fields)

    def _parse_field(self) -> FieldNode:
        name = self._parse_name()
        self._skip_ws()

        # collect annotations before children block
        post = sendto = expose = None
        while True:
            saw = self._peek()
            if not _is_name_start(saw):
                break
            # peek full word without consuming
            start = self.pos
            word = self._parse_name()
            if word in _ANNOTATIONS:
                if word == "post":
                    post = self._parse_annotation_value(word)
                elif word == "sendto":
                    sendto = self._parse_annotation_value(word)
                elif word == "expose":
                    expose = self._parse_annotation_value(word)
                self._skip_ws()
            else:
                # name not an annotation keyword → rewind, it's a sibling field
                self.pos = start
                break

        # optional children block
        children: dict[str, FieldNode] = {}
        self._skip_ws()
        if self._peek() == "{":
            self._advance()  # skip {
            self._skip_ws()
            while self.pos < self.end and self._peek() != "}":
                child = self._parse_field()
                if child.name in children:
                    self._error(f"duplicate field '{child.name}' in '{name}'")
                children[child.name] = child
                self._skip_ws()
            if self._peek() != "}":
                self._error("expected '}'")
            self._advance()  # skip }

        return FieldNode(name=name, post=post, sendto=sendto, expose=expose, children=children)

    def _parse_name(self) -> str:
        """Parse a bare [a-zA-Z_][a-zA-Z0-9_]* name."""
        ch = self._peek()
        if not _is_name_start(ch):
            self._error(f"expected name, got {repr(ch)}")
        start = self.pos
        self._advance()
        while self.pos < self.end and _is_name_cont(self.src[self.pos]):
            self._advance()
        return self.src[start:self.pos]

    def _parse_annotation_value(self, keyword: str) -> str:
        """Parse the single name argument that follows an annotation keyword."""
        self._skip_ws()
        return self._parse_name()


def parse(source: str) -> QueryDocument:
    """Parse the source string into a QueryDocument.

    Raises ParseError on invalid input.
    """
    parser = Parser(source)
    return parser.parse_document()
