# backend/app/tools/base.py
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    impl: Callable[..., Awaitable[Any]]

    @property
    def schema(self) -> dict[str, Any]:
        """The Anthropic tool-use schema (what we pass into messages.create)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
