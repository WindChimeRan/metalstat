"""Power metrics: CPU/GPU/ANE/Package power from IOReport Energy Model.

Energy Model channels report energy consumed during the sample period.
Units vary per channel (checked via IOReportChannelGetUnitLabel):
  - "mJ" (millijoules) — most component channels (CPU Energy, GPU0, ANE0, etc.)
  - "nJ" (nanojoules) — some aggregate channels (GPU Energy)

Key channels on Apple Silicon:
  - "CPU Energy"  (mJ) — total CPU energy
  - "GPU0"        (mJ) — GPU energy (component level)
  - "ANE0"        (mJ) — ANE energy
  - "DRAM0"       (mJ) — DRAM energy
  - "GPU Energy"  (nJ) — GPU energy (aggregate, nJ)

Power (W) = energy / duration.
"""

from __future__ import annotations

from dataclasses import dataclass

from metalstat.ioreport import IOReportChannel


@dataclass
class PowerMetrics:
    cpu_w: float | None = None
    gpu_w: float | None = None
    ane_w: float | None = None
    dram_w: float | None = None
    package_w: float | None = None
    available: bool = True


def _energy_to_joules(value: int, unit: str | None) -> float:
    """Convert an energy value to joules based on its unit label."""
    if unit == "mJ":
        return value / 1e3
    elif unit == "nJ":
        return value / 1e9
    elif unit == "uJ":
        return value / 1e6
    else:
        # Default: assume mJ (most common on Apple Silicon)
        return value / 1e3


def parse_power_metrics(channels: list[IOReportChannel],
                        sample_duration_ms: float) -> PowerMetrics:
    """Extract power metrics from IOReport delta channels.

    Uses specific channel names rather than substring matching to avoid
    double-counting (e.g., "GPU0" vs "GPU Energy" vs per-core "PACC0_CPU0").
    """
    if sample_duration_ms <= 0:
        return PowerMetrics(available=False)

    duration_s = sample_duration_ms / 1000.0

    # Collect energy values (in joules) for specific channels
    cpu_j: float | None = None
    gpu_j: float | None = None
    ane_j: float | None = None
    dram_j: float | None = None

    for ch in channels:
        if ch.group != "Energy Model":
            continue
        if ch.int_value is None or ch.int_value == 0:
            continue

        name = ch.channel_name
        joules = _energy_to_joules(ch.int_value, ch.unit)

        # Use the aggregate summary channels, not per-core ones
        if name == "CPU Energy":
            cpu_j = joules
        elif name == "GPU0":
            gpu_j = joules
        elif name == "ANE0":
            ane_j = joules
        elif name == "DRAM0":
            dram_j = joules

    def to_watts(j: float | None) -> float | None:
        if j is None or j <= 0:
            return None
        return j / duration_s

    cpu_w = to_watts(cpu_j)
    gpu_w = to_watts(gpu_j)
    ane_w = to_watts(ane_j)
    dram_w = to_watts(dram_j)

    # Package = sum of available components
    pkg_components = [v for v in [cpu_w, gpu_w, ane_w, dram_w] if v is not None]
    pkg_w = sum(pkg_components) if pkg_components else None

    return PowerMetrics(
        cpu_w=cpu_w,
        gpu_w=gpu_w,
        ane_w=ane_w,
        dram_w=dram_w,
        package_w=pkg_w,
        available=any(v is not None for v in [cpu_w, gpu_w, ane_w, pkg_w]),
    )
