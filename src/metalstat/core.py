"""Core module: orchestrates queries, holds all metrics, formats output."""

from __future__ import annotations

import json
import socket
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import IO, Any

import blessed

from metalstat.cpu import CPUMetrics, get_cpu_metrics
from metalstat.gpu import GPUMetrics, parse_gpu_metrics
from metalstat.memory import MemoryMetrics, get_memory_metrics
from metalstat.power import PowerMetrics, parse_power_metrics
from metalstat.sysinfo import ChipInfo, get_chip_info, get_gpu_dvfs_freqs, is_apple_silicon
from metalstat.util import (
    bytes_to_gib,
    colored_percent,
    colored_power,
    format_gib,
    pressure_indicator,
)


@dataclass
class DisplayOptions:
    show_cpu: bool = False
    show_power: bool = False
    show_memory_detail: bool = False
    show_gpu_mem: bool = False
    show_swap: bool = False
    show_temp: bool = False
    show_ane: bool = False
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

    @staticmethod
    def new_query(
        sample_duration: float = 0.2,
        query_cpu: bool = True,
        query_gpu: bool = True,
        query_power: bool = True,
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
                # IOReport unavailable — degrade gracefully
                if query_gpu:
                    gpu_metrics = GPUMetrics(
                        utilization=None, frequency_mhz=None, available=False,
                    )
                if query_power:
                    power_metrics = PowerMetrics(available=False)

        return AppleSiliconStat(
            hostname=socket.gethostname(),
            query_time=datetime.now(),
            chip=chip,
            memory=memory,
            cpu=cpu_metrics,
            gpu=gpu_metrics,
            power=power_metrics,
        )

    def _is_detailed(self, opts: DisplayOptions) -> bool:
        return any([
            opts.show_cpu, opts.show_power, opts.show_memory_detail,
            opts.show_gpu_mem, opts.show_swap, opts.show_ane,
        ])

    def print_formatted(
        self,
        opts: DisplayOptions,
        fp: IO[str] = sys.stdout,
    ) -> None:
        """Print formatted, colored output."""
        term = blessed.Terminal()
        if not opts.color:
            term = blessed.Terminal(force_styling=False)

        if self._is_detailed(opts):
            self._print_detailed(term, opts, fp)
        else:
            self._print_oneliner(term, opts, fp)

    def _print_oneliner(
        self,
        term: blessed.Terminal,
        opts: DisplayOptions,
        fp: IO[str],
    ) -> None:
        """Compact one-liner output (default, no detail flags)."""
        lines: list[str] = []

        if opts.header:
            ts = self.query_time.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"{term.bold(self.hostname)}  {ts}")

        parts: list[str] = [term.bold_cyan(self.chip.name)]

        if self.gpu and self.gpu.available:
            gpu_str = "GPU " + colored_percent(
                term, self.gpu.utilization or 0, low=30, high=80,
            )
            if self.gpu.frequency_mhz:
                gpu_str += f", {self.gpu.frequency_mhz} MHz"
            parts.append(gpu_str)
        elif self.gpu is not None:
            parts.append("GPU " + term.dim("??"))

        mem = self.memory
        parts.append(f"{bytes_to_gib(mem.used):.1f} / {bytes_to_gib(mem.total):.1f} GB")
        parts.append("Pressure: " + pressure_indicator(term, mem.pressure_level))

        lines.append(" | ".join(parts))
        print("\n".join(lines), file=fp)

    def _print_detailed(
        self,
        term: blessed.Terminal,
        opts: DisplayOptions,
        fp: IO[str],
    ) -> None:
        """Table-like multi-line output when detail flags are on."""
        L = 10  # label column width
        lines: list[str] = []
        mem = self.memory

        # Header
        if opts.header:
            ts = self.query_time.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"{term.bold(self.hostname)}  {ts}")

        # Chip title line
        chip_str = term.bold_cyan(self.chip.name)
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
        lines.append(f"{chip_str}  {term.dim(' / '.join(core_parts))}")

        # GPU row
        if self.gpu and self.gpu.available:
            val = colored_percent(term, self.gpu.utilization or 0, low=30, high=80)
            cells = [val]
            if self.gpu.frequency_mhz:
                cells.append(f"{self.gpu.frequency_mhz:>5d} MHz")
            if opts.show_power and self.power and self.power.gpu_w is not None:
                cells.append(colored_power(term, self.power.gpu_w))
            lines.append(f"  {term.bold('GPU'):>{L}}   {'   '.join(cells)}")
        elif self.gpu is not None:
            lines.append(f"  {term.bold('GPU'):>{L}}   {term.dim('unavailable')}")

        # CPU row
        if opts.show_cpu and self.cpu:
            val = colored_percent(term, self.cpu.utilization_total, low=50, high=85)
            cells = [val]
            if (
                self.cpu.utilization_p_cluster is not None
                and self.cpu.utilization_e_cluster is not None
            ):
                cells.append(
                    f"P: {self.cpu.utilization_p_cluster:4.0f}%   "
                    f"E: {self.cpu.utilization_e_cluster:4.0f}%"
                )
            if opts.show_power and self.power and self.power.cpu_w is not None:
                cells.append(colored_power(term, self.power.cpu_w))
            lines.append(f"  {term.bold('CPU'):>{L}}   {'   '.join(cells)}")

        # Memory row
        used_g = bytes_to_gib(mem.used)
        total_g = bytes_to_gib(mem.total)
        mem_cells = [f"{used_g:.1f} / {total_g:.1f} GB"]
        mem_cells.append(pressure_indicator(term, mem.pressure_level))
        lines.append(f"  {term.bold('Memory'):>{L}}   {'   '.join(mem_cells)}")

        # Memory detail sub-row
        if opts.show_memory_detail:
            parts = [
                f"{format_gib(mem.wired)}G {term.dim('wired')}",
                f"{format_gib(mem.active)}G {term.dim('active')}",
                f"{format_gib(mem.inactive)}G {term.dim('inactive')}",
                f"{format_gib(mem.compressed)}G {term.dim('compressed')}",
            ]
            lines.append(f"  {'':>{L}}   {' / '.join(parts)}")

        # Metal GPU memory row
        if opts.show_gpu_mem and mem.metal_recommended_max > 0:
            lines.append(
                f"  {term.bold('Metal'):>{L}}   "
                f"{format_gib(mem.metal_allocated)}G / "
                f"{format_gib(mem.metal_recommended_max)}G"
            )

        # Swap row
        if opts.show_swap:
            lines.append(
                f"  {term.bold('Swap'):>{L}}   "
                f"{format_gib(mem.swap_used)}G / "
                f"{format_gib(mem.swap_total)}G"
            )

        # Power summary row
        if opts.show_power and self.power and self.power.available:
            pw = self.power
            power_cells: list[str] = []
            if pw.package_w is not None:
                power_cells.append(f"{term.dim('Pkg')} {colored_power(term, pw.package_w)}")
            if pw.cpu_w is not None:
                power_cells.append(f"{term.dim('CPU')} {colored_power(term, pw.cpu_w)}")
            if pw.gpu_w is not None:
                power_cells.append(f"{term.dim('GPU')} {colored_power(term, pw.gpu_w)}")
            if pw.dram_w is not None:
                power_cells.append(f"{term.dim('DRAM')} {colored_power(term, pw.dram_w)}")
            if opts.show_ane and pw.ane_w is not None:
                power_cells.append(f"{term.dim('ANE')} {colored_power(term, pw.ane_w)}")
            if power_cells:
                lines.append(f"  {term.bold('Power'):>{L}}   {'   '.join(power_cells)}")
        elif opts.show_ane and self.power and self.power.ane_w is not None:
            lines.append(
                f"  {term.bold('ANE'):>{L}}   {colored_power(term, self.power.ane_w)}"
            )

        print("\n".join(lines), file=fp)

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

        return d

    def print_json(self, fp: IO[str] = sys.stdout) -> None:
        """Print JSON output."""
        print(json.dumps(self.to_dict(), indent=2), file=fp)
