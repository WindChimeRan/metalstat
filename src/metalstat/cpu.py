"""CPU metrics: utilization per-core and per-cluster."""

from __future__ import annotations

from dataclasses import dataclass, field

import psutil

from metalstat.sysinfo import get_chip_info


@dataclass
class CPUMetrics:
    utilization_total: float  # overall CPU utilization %
    utilization_per_core: list[float] = field(default_factory=list)
    # Per-cluster utilization (estimated from per-core data)
    utilization_p_cluster: float | None = None
    utilization_e_cluster: float | None = None
    # Core counts (for context)
    p_cores: int = 0
    e_cores: int = 0


def get_cpu_metrics() -> CPUMetrics:
    """Query CPU utilization metrics."""
    chip = get_chip_info()

    per_core = psutil.cpu_percent(interval=0.1, percpu=True)
    total = psutil.cpu_percent(interval=None)

    p_cores = chip.cpu_cores_performance
    e_cores = chip.cpu_cores_efficiency

    # Estimate per-cluster utilization
    # On Apple Silicon, P-cores are typically the first cores
    p_util = None
    e_util = None
    if p_cores > 0 and e_cores > 0 and len(per_core) >= (p_cores + e_cores):
        # Performance cores come first (perflevel0 = performance)
        p_util = sum(per_core[:p_cores]) / p_cores
        e_util = sum(per_core[p_cores:p_cores + e_cores]) / e_cores

    return CPUMetrics(
        utilization_total=total,
        utilization_per_core=per_core,
        utilization_p_cluster=p_util,
        utilization_e_cluster=e_util,
        p_cores=p_cores,
        e_cores=e_cores,
    )
