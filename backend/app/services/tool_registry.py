"""Local tool registry.

Tools are declared here once and referenced by name from agent configuration.
Two rules hold for every tool, and they are enforced by the runtime rather than
by each tool:

1. An agent may only execute a tool listed in its own `tools` allowlist.
2. Arguments are validated against the tool's JSON Schema before execution.

Nothing here shells out, imports dynamically, or evaluates model-supplied code.
"""

from __future__ import annotations

import ast
import operator
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.services.llm.base import ToolSpec


class ToolError(Exception):
    """A tool failed in a way the model should be told about."""


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> str:
        """Run the tool. Returns a string the model can read."""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    def validate(self, arguments: dict[str, Any]) -> None:
        try:
            Draft202012Validator(self.input_schema).validate(arguments)
        except ValidationError as exc:
            raise ToolError(f"Invalid arguments: {exc.message}") from exc


# ---------------------------------------------------------------------------
# Demo tools
# ---------------------------------------------------------------------------


class CalculatorTool(BaseTool):
    """Arithmetic over a parsed AST.

    Deliberately not `eval()`. The model's string is parsed, then every node is
    checked against an allowlist, so an input like `__import__("os").system(...)`
    is rejected at the node level rather than executed. Only the five binary
    operators and unary +/- below can run.
    """

    name = "calculator"
    description = (
        "Evaluate a basic arithmetic expression. Supports + - * / % ** and "
        "parentheses over numbers only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "e.g. '((3 + 4) * 12) / 2'",
                "maxLength": 200,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }

    _OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    # Bound the exponent so `9**9**9` cannot wedge the event loop.
    _MAX_EXPONENT = 64

    def _eval(self, node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return self._eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)
            ):
                raise ToolError("Only numeric literals are allowed.")
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self._eval(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in self._OPERATORS:
            left, right = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > self._MAX_EXPONENT:
                raise ToolError(f"Exponent too large (max {self._MAX_EXPONENT}).")
            if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
                raise ToolError("Division by zero.")
            return self._OPERATORS[type(node.op)](left, right)
        raise ToolError(
            f"Unsupported expression element: {type(node).__name__}. "
            "Only numbers and + - * / % ** are allowed."
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        expression = arguments["expression"]
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError(f"Could not parse expression: {exc.msg}") from exc
        return str(self._eval(tree))


class CurrentTimeTool(BaseTool):
    name = "current_time"
    description = "Get the current UTC date and time in ISO 8601 format."
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs_for(self, allowed: list[str]) -> list[ToolSpec]:
        """Specs for the named tools only. Unknown names are ignored."""
        return [self._tools[n].spec() for n in allowed if n in self._tools]

    def catalogue(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]


registry = ToolRegistry([CalculatorTool(), CurrentTimeTool()])
