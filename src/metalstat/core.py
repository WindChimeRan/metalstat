"""Core module: orchestrates queries, holds all metrics, formats output."""

from __future__ import annotations

import json
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any

from rich.console import Console, Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from metalstat.cpu import CPUMetrics, get_cpu_metrics
from metalstat.gpu import GPUMetrics, parse_gpu_metrics
from metalstat.memory import MemoryMetrics, get_memory_metrics
from metalstat.power import PowerMetrics, parse_power_metrics
from metalstat.procs import ProcessInfo, get_top_processes, _format_gpu_time
from metalstat.sysinfo import ChipInfo, get_chip_info, get_gpu_dvfs_freqs, is_apple_silicon
from metalstat.util import bytes_to_gib, format_gib, pct_style, pressure_style


@dataclass
class DisplayOptions:
    show_cpu: bool = False
    show_power: bool = False
    show_memory_detail: bool = False
    show_gpu_mem: bool = False
    show_swap: bool = False
    show_temp: bool = False
    show_ane: bool = False
    show_procs: bool = False
    num_procs: int = 8
    color: bool = True
    header: bool = True
    json_output: bool = False


_sampler_cache: dict[str, Any] = {}


def _get_sampler(groups: list[str]) -> Any:
    """Get or create a cached IOReport sampler for the given groups."""
    key = ",".join(sorted(groups))
    if key not in _sampler_cache:
        from metalstat.ioreport import IOReportSampler
        _sampler_cache[key] = IOReportSampler(groups=groups)
    return _sampler_cache[key]


# --- Rich helpers ---

def _styled_pct(value: float, low: float = 30.0, high: float = 80.0) -> Text:
    """Create a colored percentage Text."""
    style = pct_style(value, low, high)
    return Text(f"{value:5.1f}%", style=style)


def _styled_power(value: float) -> Text:
    return Text(f"{value:.1f}W", style="magenta")


def _styled_pressure(level: str) -> Text:
    style = pressure_style(level)
    return Text(f"●{level}", style=style)


@dataclass
class AppleSiliconStat:
    """A snapshot of Apple Silicon system metrics."""

    hostname: str
    query_time: datetime
    chip: ChipInfo
    memory: MemoryMetrics
    cpu: CPUMetrics | None = None
    gpu: GPUMetrics | None = None
    power: PowerMetrics | None = None
    top_procs: list[ProcessInfo] | None = None

    @staticmethod
    def new_query(
        sample_duration: float = 0.2,
        query_cpu: bool = True,
        query_gpu: bool = True,
        query_power: bool = True,
        query_procs: int = 0,
    ) -> AppleSiliconStat:
        """Query all metrics and return a new snapshot."""
        chip = get_chip_info()
        memory = get_memory_metrics()

        cpu_metrics = None
        if query_cpu:
            cpu_metrics = get_cpu_metrics()

        gpu_metrics = None
        power_metrics = None

        if query_gpu or query_power:
            try:
                groups = []
                if query_gpu:
                    groups.append("GPU Stats")
                if query_power:
                    groups.append("Energy Model")

                sampler = _get_sampler(groups)
                t0 = time.monotonic()
                channels = sampler.sample_delta(interval=sample_duration)
                duration_ms = (time.monotonic() - t0) * 1000

                if query_gpu:
                    gpu_freqs = get_gpu_dvfs_freqs()
                    gpu_metrics = parse_gpu_metrics(channels, gpu_freqs_mhz=gpu_freqs)
                if query_power:
                    power_metrics = parse_power_metrics(channels, duration_ms)
            except Exception:
                if query_gpu:
                    gpu_metrics = GPUMetrics(
                        utilization=None, frequency_mhz=None, available=False,
                    )
                if query_power:
                    power_metrics = PowerMetrics(available=False)

        procs = None
        if query_procs > 0:
            procs = get_top_processes(n=query_procs)

        return AppleSiliconStat(
            hostname=socket.gethostname(),
            query_time=datetime.now(),
            chip=chip,
            memory=memory,
            cpu=cpu_metrics,
            gpu=gpu_metrics,
            power=power_metrics,
            top_procs=procs,
        )

    def _is_detailed(self, opts: DisplayOptions) -> bool:
        return any([
            opts.show_cpu, opts.show_power, opts.show_memory_detail,
            opts.show_gpu_mem, opts.show_swap, opts.show_ane, opts.show_procs,
        ])

    def print_formatted(
        self,
        opts: DisplayOptions,
        fp: IO[str] = sys.stdout,
    ) -> None:
        """Print formatted output using rich."""
        console = Console(
            file=fp,
            force_terminal=opts.color if opts.color else None,
            no_color=not opts.color,
            highlight=False,
        )

        if self._is_detailed(opts):
            self._print_detailed(console, opts)
        else:
            self._print_oneliner(console, opts)

    def _print_oneliner(self, console: Console, opts: DisplayOptions) -> None:
        """Compact one-liner output (default, no detail flags)."""
        if opts.header:
            ts = self.query_time.strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[bold]{self.hostname}[/]  {ts}")

        parts = Text()
        parts.append(self.chip.name, style="bold cyan")

        # GPU
        if self.gpu and self.gpu.available:
            parts.append(" | GPU ")
            parts.append_text(_styled_pct(self.gpu.utilization or 0, low=30, high=80))
            if self.gpu.frequency_mhz:
                parts.append(f", {self.gpu.frequency_mhz} MHz")
        elif self.gpu is not None:
            parts.append(" | GPU ")
            parts.append("??", style="dim")

        # Memory
        mem = self.memory
        parts.append(f" | {bytes_to_gib(mem.used):.1f} / {bytes_to_gib(mem.total):.1f} GB")

        # Pressure
        parts.append(" | Pressure: ")
        parts.append_text(_styled_pressure(mem.pressure_level))

        console.print(parts)

    def _print_detailed(self, console: Console, opts: DisplayOptions) -> None:
        """Table layout with separator lines between logical sections."""
        mem = self.memory
        rule_style = "dim"

        # Header
        if opts.header:
            ts = self.query_time.strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[bold]{self.hostname}[/]  {ts}")

        # Chip title
        core_parts: list[str] = []
        if self.chip.cpu_cores_performance > 0 and self.chip.cpu_cores_efficiency > 0:
            core_parts.append(
                f"{self.chip.cpu_cores_total}C CPU "
                f"({self.chip.cpu_cores_performance}P + "
                f"{self.chip.cpu_cores_efficiency}E)"
            )
        else:
            core_parts.append(f"{self.chip.cpu_cores_total}C CPU")
        if self.chip.gpu_cores:
            core_parts.append(f"{self.chip.gpu_cores}C GPU")
        if self.chip.metal_family:
            core_parts.append(self.chip.metal_family)

        console.print(
            Text(self.chip.name, style="bold cyan")
            + Text("  " + " / ".join(core_parts), style="dim")
        )

        # Helper to create a section table (consistent column layout)
        def _section() -> Table:
            t = Table(
                show_header=False, show_edge=False, show_lines=False,
                box=None, padding=(0, 1), pad_edge=False,
            )
            t.add_column("label", style="bold", justify="right", min_width=8)
            t.add_column("value", no_wrap=True)
            return t

        # --- Compute section (GPU + CPU) ---
        console.print(Rule(style=rule_style))
        compute = _section()

        if self.gpu and self.gpu.available:
            val = Text()
            val.append_text(_styled_pct(self.gpu.utilization or 0, low=30, high=80))
            if self.gpu.frequency_mhz:
                val.append(f"   {self.gpu.frequency_mhz:>5d} MHz")
            if opts.show_power and self.power and self.power.gpu_w is not None:
                val.append("   ")
                val.append_text(_styled_power(self.power.gpu_w))
            compute.add_row("GPU", val)
        elif self.gpu is not None:
            compute.add_row("GPU", Text("unavailable", style="dim"))

        if opts.show_cpu and self.cpu:
            val = Text()
            val.append_text(_styled_pct(self.cpu.utilization_total, low=50, high=85))
            if (
                self.cpu.utilization_p_cluster is not None
                and self.cpu.utilization_e_cluster is not None
            ):
                val.append(
                    f"   P: {self.cpu.utilization_p_cluster:4.0f}%"
                    f"   E: {self.cpu.utilization_e_cluster:4.0f}%"
                )
            if opts.show_power and self.power and self.power.cpu_w is not None:
                val.append("   ")
                val.append_text(_styled_power(self.power.cpu_w))
            compute.add_row("CPU", val)

        console.print(compute)

        # --- Memory section ---
        console.print(Rule(style=rule_style))
        memory_tbl = _section()

        mem_val = Text()
        mem_val.append(f"{bytes_to_gib(mem.used):.1f} / {bytes_to_gib(mem.total):.1f} GB")
        mem_val.append("   ")
        mem_val.append_text(_styled_pressure(mem.pressure_level))
        memory_tbl.add_row("Memory", mem_val)

        if opts.show_memory_detail:
            detail = Text()
            detail.append(f"{format_gib(mem.wired)}G ")
            detail.append("wired", style="dim")
            detail.append(f" / {format_gib(mem.active)}G ")
            detail.append("active", style="dim")
            detail.append(f" / {format_gib(mem.inactive)}G ")
            detail.append("inactive", style="dim")
            detail.append(f" / {format_gib(mem.compressed)}G ")
            detail.append("compressed", style="dim")
            memory_tbl.add_row("", detail)

        if opts.show_gpu_mem and mem.metal_recommended_max > 0:
            memory_tbl.add_row(
                "Metal",
                f"{format_gib(mem.metal_allocated)}G / {format_gib(mem.metal_recommended_max)}G",
            )

        if opts.show_swap:
            memory_tbl.add_row(
                "Swap",
                f"{format_gib(mem.swap_used)}G / {format_gib(mem.swap_total)}G",
            )

        console.print(memory_tbl)

        # --- Power section ---
        has_power_row = False
        if opts.show_power and self.power and self.power.available:
            pw = self.power
            val = Text()
            items: list[tuple[str, float | None]] = [
                ("Pkg", pw.package_w),
                ("CPU", pw.cpu_w),
                ("GPU", pw.gpu_w),
                ("DRAM", pw.dram_w),
            ]
            if opts.show_ane:
                items.append(("ANE", pw.ane_w))
            first = True
            for label, watts in items:
                if watts is not None:
                    if not first:
                        val.append("   ")
                    val.append(f"{label} ", style="dim")
                    val.append_text(_styled_power(watts))
                    first = False
            if not first:
                console.print(Rule(style=rule_style))
                power_tbl = _section()
                power_tbl.add_row("Power", val)
                console.print(power_tbl)
                has_power_row = True

        if not has_power_row and opts.show_ane and self.power and self.power.ane_w is not None:
            console.print(Rule(style=rule_style))
            ane_tbl = _section()
            val = Text()
            val.append_text(_styled_power(self.power.ane_w))
            ane_tbl.add_row("ANE", val)
            console.print(ane_tbl)

        # --- Processes section ---
        if opts.show_procs and self.top_procs:
            console.print(Rule(style=rule_style))
            proc_tbl = Table(
                show_header=True, show_edge=False, show_lines=False,
                box=None, padding=(0, 1), pad_edge=False,
            )
            proc_tbl.add_column("#", style="dim", justify="right", width=3)
            proc_tbl.add_column("Process", style="bold", min_width=16, no_wrap=True)
            proc_tbl.add_column("PID", style="dim", justify="right")
            proc_tbl.add_column("RSS", justify="right")
            proc_tbl.add_column("CPU%", justify="right")
            proc_tbl.add_column("GPU Time", justify="right", style="cyan")

            for i, p in enumerate(self.top_procs, 1):
                rss_gib = bytes_to_gib(p.rss_bytes)
                if rss_gib >= 1.0:
                    rss_str = f"{rss_gib:.1f}G"
                else:
                    rss_str = f"{p.rss_bytes / (1024**2):.0f}M"
                cpu_str = f"{p.cpu_percent:.0f}%"
                gpu_str = _format_gpu_time(p.gpu_time_ns)
                proc_tbl.add_row(
                    str(i), p.name, str(p.pid), rss_str, cpu_str, gpu_str,
                )

            console.print(proc_tbl)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        d: dict[str, Any] = {
            "hostname": self.hostname,
            "query_time": self.query_time.isoformat(),
            "chip": {
                "name": self.chip.name,
                "cpu_cores": {
                    "total": self.chip.cpu_cores_total,
                    "performance": self.chip.cpu_cores_performance,
                    "efficiency": self.chip.cpu_cores_efficiency,
                },
                "gpu_cores": self.chip.gpu_cores,
                "metal_family": self.chip.metal_family,
            },
            "memory": {
                "total_gb": round(bytes_to_gib(self.memory.total), 2),
                "used_gb": round(bytes_to_gib(self.memory.used), 2),
                "available_gb": round(bytes_to_gib(self.memory.available), 2),
                "wired_gb": round(bytes_to_gib(self.memory.wired), 2),
                "active_gb": round(bytes_to_gib(self.memory.active), 2),
                "inactive_gb": round(bytes_to_gib(self.memory.inactive), 2),
                "compressed_gb": round(bytes_to_gib(self.memory.compressed), 2),
                "pressure_percent": round(self.memory.pressure_percent, 1),
                "pressure_level": self.memory.pressure_level,
            },
            "swap": {
                "used_gb": round(bytes_to_gib(self.memory.swap_used), 2),
                "total_gb": round(bytes_to_gib(self.memory.swap_total), 2),
            },
            "gpu_memory": {
                "allocated_gb": round(
                    bytes_to_gib(self.memory.metal_allocated), 2
                ),
                "recommended_max_gb": round(
                    bytes_to_gib(self.memory.metal_recommended_max), 2
                ),
            },
        }

        if self.gpu:
            d["gpu"] = {
                "utilization": (
                    round(self.gpu.utilization, 1)
                    if self.gpu.utilization is not None
                    else None
                ),
                "frequency_mhz": self.gpu.frequency_mhz,
            }

        if self.cpu:
            d["cpu"] = {
                "utilization": round(self.cpu.utilization_total, 1),
                "utilization_p_cluster": (
                    round(self.cpu.utilization_p_cluster, 1)
                    if self.cpu.utilization_p_cluster is not None
                    else None
                ),
                "utilization_e_cluster": (
                    round(self.cpu.utilization_e_cluster, 1)
                    if self.cpu.utilization_e_cluster is not None
                    else None
                ),
            }

        if self.power:
            d["power"] = {
                "cpu_w": (
                    round(self.power.cpu_w, 2)
                    if self.power.cpu_w is not None
                    else None
                ),
                "gpu_w": (
                    round(self.power.gpu_w, 2)
                    if self.power.gpu_w is not None
                    else None
                ),
                "ane_w": (
                    round(self.power.ane_w, 2)
                    if self.power.ane_w is not None
                    else None
                ),
                "dram_w": (
                    round(self.power.dram_w, 2)
                    if self.power.dram_w is not None
                    else None
                ),
                "package_w": (
                    round(self.power.package_w, 2)
                    if self.power.package_w is not None
                    else None
                ),
            }

        if self.top_procs:
            d["top_processes"] = [
                {
                    "pid": p.pid,
                    "name": p.name,
                    "rss_gb": round(bytes_to_gib(p.rss_bytes), 2),
                    "cpu_percent": round(p.cpu_percent, 1),
                    "gpu_time_ns": p.gpu_time_ns,
                }
                for p in self.top_procs
            ]

        return d

    def print_json(self, fp: IO[str] = sys.stdout) -> None:
        """Print JSON output."""
        print(json.dumps(self.to_dict(), indent=2), file=fp)
