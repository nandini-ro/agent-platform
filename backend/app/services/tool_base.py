"""The contract every tool implements, local or user-defined.

Kept apart from the registry so tool modules can build on it without
importing the registry that collects them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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
