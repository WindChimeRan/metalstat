"""Static system information: chip name, core counts, Metal device properties."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from functools import lru_cache


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
    """Get chip name, trying Metal first, then sysctl."""
    try:
        from Metal import MTLCreateSystemDefaultDevice
        device = MTLCreateSystemDefaultDevice()
        if device:
            return device.name()
    except ImportError:
        pass

    # Fallback to sysctl
    brand = _sysctl("machdep.cpu.brand_string")
    if brand:
        return brand
    return f"Apple {platform.processor()}"


def _get_metal_info() -> tuple[int | None, int | None, str | None]:
    """Get Metal device info: (recommended_max_bytes, current_alloc_bytes, family)."""
    try:
        from Metal import MTLCreateSystemDefaultDevice
        device = MTLCreateSystemDefaultDevice()
        if device:
            rec_max = device.recommendedMaxWorkingSetSize()
            cur_alloc = device.currentAllocatedSize()
            # Metal family detection
            family = None
            # Try common families (Metal 3, Apple family 9, etc.)
            try:
                # MTLGPUFamily values
                if device.supportsFamily_(1009):  # MTLGPUFamilyApple9
                    family = "Metal 3, Apple Family 9"
                elif device.supportsFamily_(1008):  # MTLGPUFamilyApple8
                    family = "Metal 3, Apple Family 8"
                elif device.supportsFamily_(1007):  # MTLGPUFamilyApple7
                    family = "Metal 2, Apple Family 7"
                else:
                    family = "Metal"
            except Exception:
                family = "Metal"
            return rec_max, cur_alloc, family
    except ImportError:
        pass
    return None, None, None


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


def get_metal_memory() -> tuple[int, int]:
    """Get Metal GPU memory: (current_allocated_bytes, recommended_max_bytes).

    Returns (0, 0) if Metal is unavailable.
    """
    try:
        from Metal import MTLCreateSystemDefaultDevice
        device = MTLCreateSystemDefaultDevice()
        if device:
            return (
                device.currentAllocatedSize(),
                device.recommendedMaxWorkingSetSize(),
            )
    except ImportError:
        pass
    return (0, 0)


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon."""
    return platform.machine() == "arm64" and platform.system() == "Darwin"
