"""Top memory-consuming processes."""

from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass
class ProcessInfo:
    pid: int
    name: str
    rss_bytes: int  # resident set size
    cpu_percent: float
    username: str


def get_top_processes(n: int = 8) -> list[ProcessInfo]:
    """Get the top N processes by RSS memory usage."""
    procs: list[ProcessInfo] = []

    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "username"]):
        try:
            info = p.info
            rss = info["memory_info"].rss if info["memory_info"] else 0
            if rss <= 0:
                continue
            procs.append(ProcessInfo(
                pid=info["pid"],
                name=info["name"] or "?",
                rss_bytes=rss,
                cpu_percent=info["cpu_percent"] or 0.0,
                username=info["username"] or "?",
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    procs.sort(key=lambda p: p.rss_bytes, reverse=True)
    return procs[:n]
