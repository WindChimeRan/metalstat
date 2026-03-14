"""GPU metrics: utilization, frequency via IOReport."""

from __future__ import annotations

from dataclasses import dataclass

from metalstat.ioreport import IOReportChannel

# Idle/off state names used by IOReport on Apple Silicon.
# States NOT in this set are considered "active" for utilization calculation.
_IDLE_STATE_NAMES = frozenset({"IDLE", "OFF", "DOWN"})

# The GPUPH channel in subgroup "GPU Performance States" reports P-state
# residencies (OFF, P1..P15). The frequency for each P-state comes from the
# IORegistry DVFS table, not from the state name. For now we compute a
# weighted-average P-state index and map it later if DVFS data is available.


@dataclass
class GPUMetrics:
    utilization: float | None  # percentage 0-100
    frequency_mhz: int | None
    # Whether data came from IOReport (vs unavailable)
    available: bool = True


def parse_gpu_metrics(
    channels: list[IOReportChannel],
    gpu_freqs_mhz: list[int] | None = None,
) -> GPUMetrics:
    """Extract GPU metrics from IOReport delta channels.

    The key channel is "GPUPH" in group "GPU Stats", subgroup
    "GPU Performance States". It has state-format data with states:
        OFF, P1, P2, ..., P15
    where OFF is idle and P1-P15 are active performance states.

    Utilization = sum(active residencies) / sum(all residencies) * 100.

    For frequency, if gpu_freqs_mhz is provided (one freq per P-state,
    ordered P1..Pn lowest to highest), we compute a weighted average.
    Otherwise frequency is reported as None.

    Args:
        channels: List of IOReportChannel from a delta sample.
        gpu_freqs_mhz: Optional list of GPU frequencies in MHz for each
            P-state (P1, P2, ...). Obtained from IORegistry DVFS table.
    """
    total_residency = 0
    active_residency = 0
    freq_weighted_sum = 0
    freq_total_residency = 0

    for ch in channels:
        if ch.group != "GPU Stats":
            continue

        # Match the GPUPH channel (primary GPU performance states).
        # SubGroup is "GPU Performance States", channel name is "GPUPH".
        if ch.channel_name == "GPUPH" and ch.subgroup == "GPU Performance States":
            if ch.state_residencies:
                # Collect active P-state residencies for utilization
                active_states: list[tuple[int, int]] = []  # (p_index, residency)
                for state_name, residency in ch.state_residencies.items():
                    total_residency += residency
                    if state_name not in _IDLE_STATE_NAMES:
                        active_residency += residency
                        # Extract P-state index (P1=0, P2=1, ...)
                        if state_name.startswith("P"):
                            try:
                                p_idx = int(state_name[1:]) - 1  # P1 -> 0
                                active_states.append((p_idx, residency))
                            except ValueError:
                                pass

                # Compute weighted-average frequency if DVFS data provided
                if gpu_freqs_mhz and active_states:
                    for p_idx, residency in active_states:
                        if 0 <= p_idx < len(gpu_freqs_mhz) and residency > 0:
                            freq_weighted_sum += gpu_freqs_mhz[p_idx] * residency
                            freq_total_residency += residency

    utilization = None
    if total_residency > 0:
        utilization = (active_residency / total_residency) * 100.0

    frequency = None
    if freq_total_residency > 0:
        frequency = int(freq_weighted_sum / freq_total_residency)

    return GPUMetrics(
        utilization=utilization,
        frequency_mhz=frequency,
        available=utilization is not None,
    )
