"""Utility functions for formatting and color."""

from __future__ import annotations

import blessed


def bytes_to_gib(b: int | float) -> float:
    return b / (1024 ** 3)


def bytes_to_mib(b: int | float) -> float:
    return b / (1024 ** 2)


def format_gib(b: int | float, precision: int = 1) -> str:
    return f"{bytes_to_gib(b):.{precision}f}"


def colored_percent(term: blessed.Terminal, value: float,
                    low: float = 30.0, high: float = 80.0) -> str:
    """Color a percentage value: green < low, yellow < high, red >= high."""
    text = f"{value:5.1f}%"
    if value < low:
        return term.green(text)
    elif value < high:
        return term.yellow(text)
    else:
        return term.red(text)


def colored_temp(term: blessed.Terminal, value: float) -> str:
    text = f"{value:.0f}°C"
    if value < 60:
        return term.green(text)
    elif value < 85:
        return term.yellow(text)
    else:
        return term.red(text)


def colored_power(term: blessed.Terminal, value: float) -> str:
    text = f"{value:.1f}W"
    return term.magenta(text)


def pressure_indicator(term: blessed.Terminal, level: str) -> str:
    """Return a colored pressure indicator."""
    if level == "green":
        return term.green("●green")
    elif level == "yellow":
        return term.yellow("●yellow")
    else:
        return term.red("●red")
