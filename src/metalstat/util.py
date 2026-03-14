"""Utility functions for formatting and unit conversion."""

from __future__ import annotations


def bytes_to_gib(b: int | float) -> float:
    return b / (1024 ** 3)


def bytes_to_mib(b: int | float) -> float:
    return b / (1024 ** 2)


def format_gib(b: int | float, precision: int = 1) -> str:
    return f"{bytes_to_gib(b):.{precision}f}"


def pct_style(value: float, low: float = 30.0, high: float = 80.0) -> str:
    """Return a rich style name for a percentage value."""
    if value < low:
        return "green"
    elif value < high:
        return "yellow"
    else:
        return "red"


def pressure_style(level: str) -> str:
    """Return a rich style for a pressure level."""
    return {"green": "green", "yellow": "yellow", "red": "red"}.get(level, "white")
