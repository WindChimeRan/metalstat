"""Top memory-consuming processes with GPU time."""

from __future__ import annotations

import ctypes
import ctypes.util
import struct
from dataclasses import dataclass

import psutil

# --- proc_pid_rusage for GPU time ---
_libc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("c"))
_RUSAGE_INFO_V4 = 4
# ri_gpu_time offset in rusage_info_v4: 248 bytes (uint64_t, nanoseconds)
_GPU_TIME_OFFSET = 248
_RUSAGE_BUF_SIZE = 512


def _get_gpu_time_ns(pid: int) -> int | None:
    """Get cumulative GPU time in nanoseconds for a process via proc_pid_rusage."""
    try:
        buf = ctypes.create_string_buffer(_RUSAGE_BUF_SIZE)
        ret = _libc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, buf)
        if ret != 0:
            return None
        return struct.unpack_from("<Q", buf.raw, _GPU_TIME_OFFSET)[0]
    except (OSError, struct.error):
        return None


@dataclass
class ProcessInfo:
    pid: int
    name: str
    rss_bytes: int  # resident set size
    cpu_percent: float
    gpu_time_ns: int | None  # cumulative GPU time in nanoseconds


def _format_gpu_time(ns: int | None) -> str:
    """Format GPU time for display."""
    if ns is None or ns == 0:
        return "-"
    secs = ns / 1e9
    if secs >= 3600:
        return f"{secs / 3600:.1f}h"
    elif secs >= 60:
        return f"{secs / 60:.1f}m"
    elif secs >= 1:
        return f"{secs:.1f}s"
    else:
        return f"{secs * 1000:.0f}ms"


def get_top_processes(n: int = 8) -> list[ProcessInfo]:
    """Get the top N processes by RSS memory usage, with GPU time."""
    procs: list[ProcessInfo] = []

    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            rss = info["memory_info"].rss if info["memory_info"] else 0
            if rss <= 0:
                continue
            gpu_ns = _get_gpu_time_ns(info["pid"])
            procs.append(ProcessInfo(
                pid=info["pid"],
                name=info["name"] or "?",
                rss_bytes=rss,
                cpu_percent=info["cpu_percent"] or 0.0,
                gpu_time_ns=gpu_ns,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    procs.sort(key=lambda p: p.rss_bytes, reverse=True)
    return procs[:n]
