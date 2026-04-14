"""Static system information: chip name, core counts, Metal device properties."""

from __future__ import annotations

import platform
import re
import struct
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from Metal import MTLCreateSystemDefaultDevice


@dataclass(frozen=True)
class ChipInfo:
    name: str  # e.g. "Apple M4 Pro"
    cpu_cores_total: int
    cpu_cores_performance: int
    cpu_cores_efficiency: int
    gpu_cores: int | None
    memory_total_bytes: int
    metal_family: str | None = None


def _sysctl(key: str) -> str:
    """Read a sysctl value."""
    r = subprocess.run(
        ["sysctl", "-n", key],
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip()


def _sysctl_int(key: str, default: int = 0) -> int:
    try:
        return int(_sysctl(key))
    except (ValueError, subprocess.SubprocessError):
        return default


def _get_gpu_core_count() -> int | None:
    """Get GPU core count from system_profiler."""
    try:
        r = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if "Total Number of Cores" in line:
                parts = line.split(":")
                if len(parts) == 2:
                    return int(parts[1].strip())
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


def _get_chip_name() -> str:
    """Get chip name from Metal device, with sysctl fallback."""
    device = MTLCreateSystemDefaultDevice()
    if device:
        return device.name()

    # Fallback to sysctl
    brand = _sysctl("machdep.cpu.brand_string")
    if brand:
        return brand
    return f"Apple {platform.processor()}"


def _get_metal_info() -> tuple[int | None, int | None, str | None]:
    """Get Metal device info: (recommended_max_bytes, current_alloc_bytes, family)."""
    device = MTLCreateSystemDefaultDevice()
    if not device:
        return None, None, None

    rec_max = device.recommendedMaxWorkingSetSize()
    cur_alloc = device.currentAllocatedSize()

    # GPU family detection (hardware capability tier)
    family = None
    # MTLGPUFamily enum values (Apple-specific families)
    # Probe from highest to lowest
    families = [
        (1009, "Apple Family 9"),   # M3/M4
        (1008, "Apple Family 8"),   # M2
        (1007, "Apple Family 7"),   # M1
    ]
    for val, label in families:
        if device.supportsFamily_(val):
            family = label
            break

    return rec_max, cur_alloc, family


@lru_cache(maxsize=1)
def get_chip_info() -> ChipInfo:
    """Get static chip information (cached for session)."""
    name = _get_chip_name()
    total_cores = _sysctl_int("hw.physicalcpu", 0)
    p_cores = _sysctl_int("hw.perflevel0.physicalcpu", 0)
    e_cores = _sysctl_int("hw.perflevel1.physicalcpu", 0)

    # If perflevel sysctl not available, all cores are "performance"
    if p_cores == 0 and e_cores == 0 and total_cores > 0:
        p_cores = total_cores

    gpu_cores = _get_gpu_core_count()
    mem_total = _sysctl_int("hw.memsize", 0)

    _, _, metal_family = _get_metal_info()

    return ChipInfo(
        name=name,
        cpu_cores_total=total_cores,
        cpu_cores_performance=p_cores,
        cpu_cores_efficiency=e_cores,
        gpu_cores=gpu_cores,
        memory_total_bytes=mem_total,
        metal_family=metal_family,
    )


def _get_gpu_memory_in_use() -> int:
    """Read system-wide in-use GPU memory (bytes) from IOAccelerator.

    MTLDevice.currentAllocatedSize is per-process and only sees resources
    created by the calling process's own MTLDevice — so a monitoring tool
    always reads ~0 even when other processes are pinning gigabytes of GPU
    memory. The IOAccelerator IORegistry node exposes the system-wide value
    under PerformanceStatistics["In use system memory"].
    """
    try:
        r = subprocess.run(
            ["ioreg", "-rc", "IOAccelerator", "-d", "1"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r'"In use system memory"\s*=\s*(\d+)', r.stdout)
        if m:
            return int(m.group(1))
    except (subprocess.SubprocessError, ValueError):
        pass
    return 0


def get_metal_memory() -> tuple[int, int]:
    """Get Metal GPU memory: (in_use_bytes, recommended_max_bytes).

    in_use_bytes is system-wide GPU memory usage from IOAccelerator.
    recommended_max_bytes is the Metal default device's recommended working
    set size (a static device property).
    """
    in_use = _get_gpu_memory_in_use()
    device = MTLCreateSystemDefaultDevice()
    rec_max = device.recommendedMaxWorkingSetSize() if device else 0
    return (in_use, rec_max)


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon."""
    return platform.machine() == "arm64" and platform.system() == "Darwin"


@lru_cache(maxsize=1)
def get_gpu_dvfs_freqs() -> list[int] | None:
    """Get GPU DVFS frequency table from IORegistry (cached).

    Returns a list of frequencies in MHz for P-states P1, P2, ..., Pn
    (ascending order, matching IOReport "GPU Stats" P-state naming).
    Returns None if the table cannot be read.
    """
    try:
        r = subprocess.run(
            ["ioreg", "-l", "-w", "0", "-p", "IODeviceTree"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return None

        # Find the sgx (GPU) node and extract perf-states
        lines = r.stdout.split("\n")
        in_sgx = False
        sgx_depth = 0
        props: dict[str, bytes] = {}

        for line in lines:
            if "sgx@" in line.lower() and "+-o" in line:
                in_sgx = True
                sgx_depth = line.count("|")
                continue

            if in_sgx:
                if "+-o" in line and line.count("|") <= sgx_depth and "sgx" not in line.lower():
                    break

                m = re.search(r'"([^"]+)"\s*=\s*<([0-9a-fA-F]+)>', line)
                if m:
                    key = m.group(1)
                    if key not in props:
                        props[key] = bytes.fromhex(m.group(2))

        if "perf-states" not in props:
            return None

        raw = props["perf-states"]
        ps_count = struct.unpack("<I", props["perf-state-count"][:4])[0] if "perf-state-count" in props else len(raw) // 8

        # Decode first table (die 0): each entry is (u32 freq_hz, u32 voltage_mv)
        freqs_hz = []
        for i in range(ps_count):
            offset = i * 8
            if offset + 8 > len(raw):
                break
            freq_hz = struct.unpack_from("<I", raw, offset)[0]
            if freq_hz > 0:
                freqs_hz.append(freq_hz)

        if not freqs_hz:
            return None

        # Sort ascending (P1 = lowest freq) and convert to MHz
        freqs_hz.sort()
        return [int(f / 1e6) for f in freqs_hz]

    except (subprocess.SubprocessError, struct.error, KeyError, ValueError):
        return None
