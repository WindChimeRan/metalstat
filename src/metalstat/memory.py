"""Memory metrics: system memory breakdown, swap, pressure, Metal GPU allocation."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import psutil

from metalstat.sysinfo import get_metal_memory


@dataclass
class MemoryMetrics:
    total: int  # bytes
    used: int
    available: int
    free: int
    active: int
    inactive: int
    wired: int
    # Compressed memory (macOS-specific, from vm_stat)
    compressed: int
    # Pressure
    pressure_percent: float
    pressure_level: str  # "green", "yellow", "red"
    # Swap
    swap_total: int
    swap_used: int
    # Metal GPU allocation
    metal_allocated: int
    metal_recommended_max: int


def _get_compressed_memory() -> int:
    """Get compressed memory bytes from vm_stat."""
    try:
        r = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5,
        )
        page_size = 16384  # Apple Silicon uses 16K pages
        for line in r.stdout.splitlines():
            if "page size of" in line:
                # "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
                parts = line.split("page size of")
                if len(parts) == 2:
                    page_size = int(parts[1].strip().split()[0])
            if "Pages occupied by compressor" in line:
                parts = line.split(":")
                if len(parts) == 2:
                    pages = int(parts[1].strip().rstrip("."))
                    return pages * page_size
    except (subprocess.SubprocessError, ValueError):
        pass
    return 0


def _get_memory_pressure() -> tuple[float, str]:
    """Get memory pressure from the system.

    Returns (pressure_percent, level) where level is "green", "yellow", or "red".
    """
    try:
        r = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            if "System-wide memory free percentage" in line:
                parts = line.split(":")
                if len(parts) == 2:
                    free_pct = float(parts[1].strip().rstrip("%"))
                    pressure_pct = 100.0 - free_pct
                    if free_pct > 50:
                        level = "green"
                    elif free_pct > 25:
                        level = "yellow"
                    else:
                        level = "red"
                    return pressure_pct, level
    except (subprocess.SubprocessError, ValueError):
        pass

    # Fallback: estimate from psutil
    mem = psutil.virtual_memory()
    pressure_pct = mem.percent
    if pressure_pct < 50:
        level = "green"
    elif pressure_pct < 75:
        level = "yellow"
    else:
        level = "red"
    return pressure_pct, level


def get_memory_metrics() -> MemoryMetrics:
    """Query all memory metrics."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    compressed = _get_compressed_memory()
    pressure_pct, pressure_level = _get_memory_pressure()

    metal_alloc, metal_max = get_metal_memory()

    return MemoryMetrics(
        total=mem.total,
        used=mem.used,
        available=mem.available,
        free=mem.free,
        active=mem.active if hasattr(mem, "active") else 0,
        inactive=mem.inactive if hasattr(mem, "inactive") else 0,
        wired=mem.wired if hasattr(mem, "wired") else 0,
        compressed=compressed,
        pressure_percent=pressure_pct,
        pressure_level=pressure_level,
        swap_total=swap.total,
        swap_used=swap.used,
        metal_allocated=metal_alloc,
        metal_recommended_max=metal_max,
    )
