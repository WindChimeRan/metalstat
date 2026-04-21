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
from metalstat.util import bytes_to_gib, format_gib, pct_style, pressure_style, round_or_none


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

    def to_sample_dict(self, start_time: float | None = None) -> dict[str, Any]:
        """Flat per-sample dict for JSONL streaming.

        `start_time` is the wall-clock reference point (unix seconds) for
        `elapsed_s`. When None, defaults to this sample's own timestamp, so a
        standalone one-shot has `elapsed_s == 0.0`.
        """
        t = self.query_time.timestamp()
        if start_time is None:
            start_time = t
        elapsed_s = t - start_time

        mem = self.memory
        gpu = self.gpu
        cpu = self.cpu
        pw = self.power

        return {
            "t": round(t, 3),
            "elapsed_s": round(elapsed_s, 3),
            "gpu_util": round_or_none(gpu.utilization if gpu else None, 1),
            "gpu_freq_mhz": gpu.frequency_mhz if gpu else None,
            "cpu_util": round_or_none(cpu.utilization_total if cpu else None, 1),
            "cpu_p_util": round_or_none(cpu.utilization_p_cluster if cpu else None, 1),
            "cpu_e_util": round_or_none(cpu.utilization_e_cluster if cpu else None, 1),
            "mem_used_gb": round(bytes_to_gib(mem.used), 2),
            "mem_wired_gb": round(bytes_to_gib(mem.wired), 2),
            "mem_active_gb": round(bytes_to_gib(mem.active), 2),
            "mem_inactive_gb": round(bytes_to_gib(mem.inactive), 2),
            "mem_compressed_gb": round(bytes_to_gib(mem.compressed), 2),
            "mem_pressure_pct": round(mem.pressure_percent, 1),
            "mem_pressure_level": mem.pressure_level,
            "gpu_mem_allocated_gb": round(bytes_to_gib(mem.metal_allocated), 2),
            "swap_used_gb": round(bytes_to_gib(mem.swap_used), 2),
            "cpu_w": round_or_none(pw.cpu_w if pw else None, 2),
            "gpu_w": round_or_none(pw.gpu_w if pw else None, 2),
            "ane_w": round_or_none(pw.ane_w if pw else None, 2),
            "dram_w": round_or_none(pw.dram_w if pw else None, 2),
            "pkg_w": round_or_none(pw.package_w if pw else None, 2),
        }

    def to_meta_dict(self) -> dict[str, Any]:
        """Static per-machine info — emit once, separately from samples."""
        mem = self.memory
        return {
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
            "memory_total_gb": round(bytes_to_gib(mem.total), 2),
            "swap_total_gb": round(bytes_to_gib(mem.swap_total), 2),
            "gpu_mem_recommended_max_gb": round(
                bytes_to_gib(mem.metal_recommended_max), 2
            ),
        }


def sample_json(sample_duration: float, start_time: float | None = None) -> str:
    """Query one sample and serialize it as a JSONL record (no trailing newline)."""
    stat = AppleSiliconStat.new_query(
        sample_duration=sample_duration,
        query_cpu=True,
        query_gpu=True,
        query_power=True,
        query_procs=0,
    )
    return json.dumps(stat.to_sample_dict(start_time=start_time))


def meta_json() -> str:
    """Gather static system info and serialize as an indented JSON object."""
    stat = AppleSiliconStat.new_query(
        sample_duration=0.0,
        query_cpu=False,
        query_gpu=False,
        query_power=False,
        query_procs=0,
    )
    return json.dumps(stat.to_meta_dict(), indent=2)
