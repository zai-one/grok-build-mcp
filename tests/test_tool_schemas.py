"""Every advertised tool schema must be JSON Schema draft 2020-12.

This is not style policing. An MCP host forwards ``inputSchema`` verbatim to its
model provider, and a provider that requires draft 2020-12 rejects the whole
request when one schema does not conform. The tool stays resident in session
state, so every following turn fails too and the session cannot recover — a
single malformed field is a denial of service for every user of the server.

The failure that motivated this file came from a neighbouring MCP server that
serialised Zod with ``target: "openApi3"`` and emitted the draft-04 form
``{"exclusiveMinimum": true, "minimum": 0}``. In draft 2020-12
``exclusiveMinimum`` is a number, so the schema is invalid and the host died.
These tests exist so the same class of defect fails here instead.
"""

from __future__ import annotations

import unittest
from typing import Any

from grok_delegate import server

# Draft-04 / OpenAPI-3.0 spellings that a 2020-12 consumer either rejects or
# silently ignores. Ignoring is not benign: the constraint quietly disappears.
BOOLEAN_IN_DRAFT4 = ("exclusiveMinimum", "exclusiveMaximum")
OPENAPI_ONLY = ("nullable", "discriminator", "xml", "externalDocs")
SUPERSEDED = {"definitions": "$defs"}

VALID_TYPES = {
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}


def _tools() -> list[dict[str, Any]]:
    """Tools exactly as the server publishes them over JSON-RPC."""
    resp = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert resp is not None
    return resp["result"]["tools"]


def _walk(node: Any, path: str):
    """Yield every (path, dict) node in a schema, including inside arrays."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


class ToolSchemaConformanceTests(unittest.TestCase):
    """Contract with the host's model provider, checked on the real payload."""

    def setUp(self) -> None:
        self.tools = _tools()
        self.assertTrue(self.tools, "tools/list returned no tools")

    def test_every_tool_declares_an_object_schema(self) -> None:
        """Providers require a top-level object schema, not a bare scalar."""
        for tool in self.tools:
            name = tool.get("name")
            self.assertTrue(name, f"tool without a name: {tool!r}")
            self.assertTrue(tool.get("description"), f"{name}: empty description")
            schema = tool.get("inputSchema")
            self.assertIsInstance(schema, dict, f"{name}: inputSchema must be an object")
            self.assertEqual(
                schema.get("type"),
                "object",
                f"{name}: inputSchema.type must be 'object', got {schema.get('type')!r}",
            )

    def test_no_boolean_exclusive_bounds(self) -> None:
        """The exact defect that bricked a host: draft-04 boolean bounds.

        Asserted on the value's type rather than its presence, because
        ``exclusiveMinimum`` is legitimate in 2020-12 — as a number.
        """
        for tool in self.tools:
            for path, node in _walk(tool["inputSchema"], f"{tool['name']}.inputSchema"):
                for keyword in BOOLEAN_IN_DRAFT4:
                    if keyword in node:
                        self.assertNotIsInstance(
                            node[keyword],
                            bool,
                            f"{path}.{keyword} is boolean (draft-04); "
                            f"draft 2020-12 requires a number",
                        )

    def test_no_openapi_or_superseded_keywords(self) -> None:
        """OpenAPI dialect keywords are not JSON Schema and drop constraints."""
        for tool in self.tools:
            for path, node in _walk(tool["inputSchema"], f"{tool['name']}.inputSchema"):
                for keyword in OPENAPI_ONLY:
                    self.assertNotIn(
                        keyword, node, f"{path}: OpenAPI-only keyword {keyword!r}"
                    )
                for old, new in SUPERSEDED.items():
                    self.assertNotIn(
                        old, node, f"{path}: {old!r} is draft-04; use {new!r}"
                    )

    def test_declared_types_are_valid(self) -> None:
        """A typo'd type is accepted by nobody and caught by no other test."""
        for tool in self.tools:
            for path, node in _walk(tool["inputSchema"], f"{tool['name']}.inputSchema"):
                if "type" not in node:
                    continue
                declared = node["type"]
                values = declared if isinstance(declared, list) else [declared]
                for value in values:
                    self.assertIn(value, VALID_TYPES, f"{path}.type: unknown {value!r}")

    def test_required_names_exist_in_properties(self) -> None:
        """A required key with no property is unsatisfiable — the call can never pass."""
        for tool in self.tools:
            for path, node in _walk(tool["inputSchema"], f"{tool['name']}.inputSchema"):
                if node.get("type") != "object" or "required" not in node:
                    continue
                properties = node.get("properties")
                if not isinstance(properties, dict):
                    self.fail(f"{path}: 'required' present without 'properties'")
                missing = sorted(set(node["required"]) - set(properties))
                self.assertFalse(missing, f"{path}: required but not defined: {missing}")

    def test_declared_dialect_is_2020_12(self) -> None:
        """$schema is optional, but naming an older dialect misleads the consumer."""
        for tool in self.tools:
            for path, node in _walk(tool["inputSchema"], f"{tool['name']}.inputSchema"):
                if "$schema" not in node:
                    continue
                self.assertIn(
                    "2020-12",
                    str(node["$schema"]),
                    f"{path}.$schema declares a pre-2020-12 dialect",
                )

    def test_validates_against_the_real_metaschema(self) -> None:
        """The strongest schema check there is, and it must not opt out.

        This used to `skipTest` when jsonschema was absent, which meant the
        hardest contract in the suite disappeared on exactly the machines that
        had not installed the test extra -- a green run that had never checked
        the thing it is named after. `scripts/routines.py` fails on a missing
        validator; the suite now agrees with it. The dependency is declared in
        `[project.optional-dependencies].test`, so the message names the cure.
        """
        try:
            from jsonschema.validators import Draft202012Validator
        except ImportError as exc:  # pragma: no cover - depends on the env
            self.fail(
                "jsonschema is required to validate the tool schemas: "
                f"install it with `pip install -e .[test]` ({exc})"
            )

        for tool in self.tools:
            with self.subTest(tool=tool["name"]):
                Draft202012Validator.check_schema(tool["inputSchema"])


if __name__ == "__main__":
    unittest.main()
