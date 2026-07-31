"""Built-in tools available to agents."""

from __future__ import annotations

import ast
import operator
from datetime import UTC, datetime
from typing import Any

from ai_gateway.application.ports.tools import Tool, ToolExecutionContext
from ai_gateway.domain.entities.agent import ToolDefinition
from ai_gateway.domain.errors import ToolError

_SAFE_OPERATORS: dict[type[ast.AST], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class CurrentTimeTool:
    """Returns the current UTC time in ISO-8601 format."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool contract."""
        return ToolDefinition(
            name="current_time",
            description="Return the current UTC time in ISO-8601 format.",
            parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
            tags=frozenset({"builtin", "time"}),
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        """Return the current UTC timestamp.

        Args:
            arguments: Unused.
            context: Invocation context.

        Returns:
            The ISO-8601 timestamp.
        """
        del arguments, context
        return datetime.now(UTC).isoformat()


class EchoTool:
    """Echoes a message back to the model."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool contract."""
        return ToolDefinition(
            name="echo",
            description="Echo a message back. Useful for tests and debugging.",
            parameters_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            tags=frozenset({"builtin", "test"}),
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        """Echo the supplied message.

        Args:
            arguments: Must contain ``message``.
            context: Invocation context.

        Returns:
            The echoed message.
        """
        del context
        message = arguments.get("message")
        if not isinstance(message, str):
            raise ToolError("echo requires a string 'message' argument")
        return message


class CalculatorTool:
    """Evaluates a restricted arithmetic expression."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool contract."""
        return ToolDefinition(
            name="calculator",
            description="Evaluate a basic arithmetic expression (+ - * / // % **).",
            parameters_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
                "additionalProperties": False,
            },
            tags=frozenset({"builtin", "math"}),
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        """Evaluate an arithmetic expression.

        Args:
            arguments: Must contain ``expression``.
            context: Invocation context.

        Returns:
            The numeric result as a string.
        """
        del context
        expression = arguments.get("expression")
        if not isinstance(expression, str):
            raise ToolError("calculator requires a string 'expression' argument")
        try:
            tree = ast.parse(expression, mode="eval")
            result = _eval_ast(tree.body)
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError, KeyError) as exc:
            raise ToolError(f"Invalid arithmetic expression: {exc}") from exc
        return str(result)


def _eval_ast(node: ast.AST) -> float:
    """Recursively evaluate a restricted AST.

    Args:
        node: Expression node.

    Returns:
        The numeric result.

    Raises:
        ValueError: If the node type is not permitted.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPERATORS[type(node.op)]
        return float(op(_eval_ast(node.left), _eval_ast(node.right)))
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPERATORS[type(node.op)]
        return float(op(_eval_ast(node.operand)))
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def build_builtin_tools() -> tuple[Tool, ...]:
    """Construct the default built-in tool set.

    Returns:
        The built-in tools.
    """
    return (CurrentTimeTool(), EchoTool(), CalculatorTool())


__all__ = ["CalculatorTool", "CurrentTimeTool", "EchoTool", "build_builtin_tools"]
