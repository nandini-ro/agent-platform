"""JSON Schema dialect translation for tool definitions.

A tool declares one JSON Schema in the registry, but the providers do not agree
on what a tool schema may contain, so each adapter translates before sending:

* OpenAI accepts standard JSON Schema; strict mode additionally requires
  ``additionalProperties: false`` and every property listed in ``required``.
* Gemini rejects a list of JSON Schema keywords outright with a 400, including
  ``title`` - which Pydantic (and therefore most MCP servers) emits on every
  property.

That last one is why this module exists. MCP tool schemas arrive from servers
we do not control, so sanitising has to happen here rather than by asking tool
authors to write Gemini-safe schemas.
"""

from __future__ import annotations

from typing import Any

# Keywords the Gemini API rejects in a function declaration. Sources:
# https://github.com/googleapis/python-genai/issues/1815
# https://github.com/BerriAI/litellm/issues/14330
GEMINI_UNSUPPORTED_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "$comment",
        "additionalProperties",
        "const",
        "default",
        "definitions",
        "examples",
        "patternProperties",
        "propertyNames",
        "title",
        "unevaluatedProperties",
    }
)

# Gemini only honours these two `format` values on strings; anything else
# (e.g. "uri", "email") is rejected.
GEMINI_STRING_FORMATS = frozenset({"enum", "date-time"})

# Keys that hold a nested schema, or a list of them.
_NESTED_SCHEMA_KEYS = ("items", "not")
_NESTED_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")


def sanitize_for_gemini(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip everything Gemini rejects, recursively.

    Returns ``None`` when the result declares no parameters: Gemini wants the
    ``parameters`` field omitted entirely rather than set to an empty object,
    which is exactly what a no-argument tool like ``current_time`` produces.
    """
    if not schema:
        return None
    cleaned = _clean(schema)
    if cleaned.get("type") == "object" and not cleaned.get("properties"):
        return None
    return cleaned


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in GEMINI_UNSUPPORTED_KEYS:
            # `const: x` carries real meaning, so keep it as a single-value
            # enum rather than dropping the constraint on the floor.
            if key == "const":
                out["enum"] = [value]
            continue

        if key == "format" and node.get("type") == "string":
            if value in GEMINI_STRING_FORMATS:
                out[key] = value
            continue

        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _clean(sub) for name, sub in value.items()}
        elif key in _NESTED_SCHEMA_KEYS:
            out[key] = _clean(value)
        elif key in _NESTED_SCHEMA_LIST_KEYS and isinstance(value, list):
            out[key] = [_clean(item) for item in value]
        else:
            out[key] = value

    # A property dropped from `properties` must not linger in `required`.
    if isinstance(out.get("required"), list) and isinstance(
        out.get("properties"), dict
    ):
        out["required"] = [r for r in out["required"] if r in out["properties"]]
        if not out["required"]:
            out.pop("required")

    return out


def sanitize_for_openai(schema: dict[str, Any] | None) -> dict[str, Any]:
    """OpenAI accepts standard JSON Schema; it only needs a well-formed object.

    Kept as its own function so the OpenAI dialect has somewhere to grow (strict
    mode, for instance) without the adapter reaching back into this module's
    internals.
    """
    if not schema:
        return {"type": "object", "properties": {}}
    return schema
