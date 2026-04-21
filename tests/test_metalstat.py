"""Tests for metalstat — run on actual Apple Silicon hardware."""

import json
import subprocess
import sys

import pytest


def run_metalstat(*args: str) -> subprocess.CompletedProcess:
    """Run metalstat CLI and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "metalstat", *args],
        capture_output=True, text=True, timeout=30,
    )


class TestSysinfo:
    def test_is_apple_silicon(self):
        from metalstat.sysinfo import is_apple_silicon
        # This test only makes sense on Apple Silicon
        import platform
        if platform.machine() == "arm64" and platform.system() == "Darwin":
            assert is_apple_silicon()

    def test_get_chip_info(self):
        from metalstat.sysinfo import get_chip_info
        chip = get_chip_info()
        assert chip.name  # non-empty
        assert "Apple" in chip.name
        assert chip.cpu_cores_total > 0
        assert chip.memory_total_bytes > 0

    def test_get_chip_info_cached(self):
        from metalstat.sysinfo import get_chip_info
        a = get_chip_info()
        b = get_chip_info()
        assert a is b  # same object (lru_cache)

    def test_gpu_core_count(self):
        from metalstat.sysinfo import get_chip_info
        chip = get_chip_info()
        assert chip.gpu_cores is None or chip.gpu_cores > 0

    def test_get_metal_memory(self):
        from metalstat.sysinfo import get_metal_memory
        alloc, max_rec = get_metal_memory()
        # Returns (0, 0) if pyobjc-framework-Metal not installed
        try:
            import Metal
            assert max_rec > 0
        except ImportError:
            assert max_rec == 0
        assert alloc >= 0

    def test_get_gpu_dvfs_freqs(self):
        from metalstat.sysinfo import get_gpu_dvfs_freqs
        freqs = get_gpu_dvfs_freqs()
        if freqs is not None:
            assert len(freqs) > 0
            assert all(f > 0 for f in freqs)
            # Should be sorted ascending
            assert freqs == sorted(freqs)


class TestMemory:
    def test_get_memory_metrics(self):
        from metalstat.memory import get_memory_metrics
        mem = get_memory_metrics()
        assert mem.total > 0
        assert mem.used > 0
        assert mem.available > 0
        assert mem.wired >= 0
        assert mem.active >= 0
        assert mem.pressure_level in ("green", "yellow", "red")
        assert 0 <= mem.pressure_percent <= 100

    def test_compressed_memory(self):
        from metalstat.memory import _get_compressed_memory
        compressed = _get_compressed_memory()
        assert compressed >= 0


class TestCPU:
    def test_get_cpu_metrics(self):
        from metalstat.cpu import get_cpu_metrics
        cpu = get_cpu_metrics()
        assert 0 <= cpu.utilization_total <= 100
        assert len(cpu.utilization_per_core) > 0
        assert cpu.p_cores > 0 or cpu.e_cores > 0


class TestIOReport:
    def test_ioreport_available(self):
        from metalstat.ioreport import IOREPORT_AVAILABLE
        assert IOREPORT_AVAILABLE

    def test_sampler_creation(self):
        from metalstat.ioreport import IOReportSampler
        sampler = IOReportSampler(groups=["GPU Stats"])
        assert sampler._subscription is not None
        assert sampler._sub_channels is not None

    def test_sample_delta(self):
        from metalstat.ioreport import IOReportSampler
        sampler = IOReportSampler(groups=["GPU Stats"])
        channels = sampler.sample_delta(interval=0.1)
        assert len(channels) > 0
        # Should have at least one GPU Stats channel
        gpu_channels = [c for c in channels if c.group == "GPU Stats"]
        assert len(gpu_channels) > 0

    def test_energy_model_channels(self):
        from metalstat.ioreport import IOReportSampler
        sampler = IOReportSampler(groups=["Energy Model"])
        channels = sampler.sample_delta(interval=0.1)
        energy_channels = [c for c in channels if c.group == "Energy Model"]
        assert len(energy_channels) > 0
        # Should have CPU Energy channel
        names = [c.channel_name for c in energy_channels]
        assert "CPU Energy" in names


class TestGPU:
    def test_parse_gpu_metrics(self):
        from metalstat.ioreport import IOReportSampler
        from metalstat.gpu import parse_gpu_metrics
        sampler = IOReportSampler(groups=["GPU Stats"])
        channels = sampler.sample_delta(interval=0.1)
        metrics = parse_gpu_metrics(channels)
        assert metrics.available
        assert metrics.utilization is not None
        assert 0 <= metrics.utilization <= 100

    def test_parse_gpu_metrics_with_dvfs(self):
        from metalstat.ioreport import IOReportSampler
        from metalstat.gpu import parse_gpu_metrics
        from metalstat.sysinfo import get_gpu_dvfs_freqs
        sampler = IOReportSampler(groups=["GPU Stats"])
        channels = sampler.sample_delta(interval=0.1)
        freqs = get_gpu_dvfs_freqs()
        metrics = parse_gpu_metrics(channels, gpu_freqs_mhz=freqs)
        assert metrics.available
        # Frequency should be set if GPU was active
        if metrics.utilization and metrics.utilization > 0:
            assert metrics.frequency_mhz is not None
            assert metrics.frequency_mhz > 0


class TestPower:
    def test_parse_power_metrics(self):
        from metalstat.ioreport import IOReportSampler
        from metalstat.power import parse_power_metrics
        import time
        sampler = IOReportSampler(groups=["Energy Model"])
        t0 = time.monotonic()
        channels = sampler.sample_delta(interval=0.2)
        duration_ms = (time.monotonic() - t0) * 1000
        metrics = parse_power_metrics(channels, duration_ms)
        assert metrics.available
        assert metrics.cpu_w is not None
        assert metrics.cpu_w > 0


class TestCore:
    def test_new_query(self):
        from metalstat.core import AppleSiliconStat
        stat = AppleSiliconStat.new_query(sample_duration=0.1)
        assert stat.hostname
        assert stat.chip.name
        assert stat.memory.total > 0
        assert stat.gpu is not None

    def test_to_meta_dict(self):
        from metalstat.core import AppleSiliconStat
        stat = AppleSiliconStat.new_query(sample_duration=0.1)
        d = stat.to_meta_dict()
        assert "hostname" in d
        assert "chip" in d
        assert d["chip"]["name"]
        assert d["memory_total_gb"] > 0
        assert d["gpu_mem_recommended_max_gb"] >= 0
        # Static-only: nothing dynamic should appear here
        assert "gpu_util" not in d
        assert "cpu_util" not in d
        assert "top_processes" not in d

    def test_to_sample_dict_flat_and_all_fields(self):
        from metalstat.core import AppleSiliconStat
        stat = AppleSiliconStat.new_query(sample_duration=0.1)
        s = stat.to_sample_dict()
        # Flat — no nested dicts
        for v in s.values():
            assert not isinstance(v, dict), f"sample dict should be flat: {s}"
        # elapsed_s defaults to 0 when no start_time supplied
        assert s["elapsed_s"] == 0.0
        assert s["t"] > 0
        # Required sample fields always present (None if unavailable)
        for key in (
            "gpu_util", "gpu_freq_mhz",
            "cpu_util", "cpu_p_util", "cpu_e_util",
            "mem_used_gb", "mem_wired_gb", "mem_active_gb",
            "mem_inactive_gb", "mem_compressed_gb",
            "mem_pressure_pct", "mem_pressure_level",
            "gpu_mem_allocated_gb", "swap_used_gb",
            "cpu_w", "gpu_w", "ane_w", "dram_w", "pkg_w",
        ):
            assert key in s, f"missing key {key}"
        assert s["mem_used_gb"] > 0
        # top_processes explicitly excluded from stream schema
        assert "top_processes" not in s
        assert "top_procs" not in s

    def test_to_sample_dict_elapsed(self):
        from metalstat.core import AppleSiliconStat
        stat = AppleSiliconStat.new_query(sample_duration=0.1)
        t = stat.query_time.timestamp()
        s = stat.to_sample_dict(start_time=t - 5.0)
        assert 4.9 <= s["elapsed_s"] <= 5.1

    def test_formatted_output(self):
        from metalstat.core import AppleSiliconStat, DisplayOptions
        import io
        stat = AppleSiliconStat.new_query(sample_duration=0.1)
        buf = io.StringIO()
        opts = DisplayOptions(color=False)
        stat.print_formatted(opts, fp=buf)
        output = buf.getvalue()
        assert "Apple" in output
        assert "GB" in output


class TestCLI:
    def test_help(self):
        r = run_metalstat("--help")
        assert r.returncode == 0
        assert "metalstat" in r.stdout

    def test_version(self):
        import re
        from metalstat import __version__
        r = run_metalstat("--version")
        assert r.returncode == 0
        assert __version__ in r.stdout
        assert re.search(r"\d+\.\d+\.\d+", r.stdout)

    def test_default_output(self):
        r = run_metalstat("--no-color")
        assert r.returncode == 0
        assert "Apple" in r.stdout
        assert "GB" in r.stdout
        assert "GPU" in r.stdout

    def test_jsonl_oneshot(self):
        r = run_metalstat("--jsonl")
        assert r.returncode == 0
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        # Flat sample schema
        assert "t" in data
        assert "elapsed_s" in data
        assert data["elapsed_s"] == 0.0
        assert "mem_used_gb" in data
        assert "gpu_util" in data

    def test_meta_json(self):
        r = run_metalstat("--meta-json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "hostname" in data
        assert data["chip"]["name"]
        assert data["memory_total_gb"] > 0
        # No dynamic fields leak in
        assert "gpu_util" not in data

    def test_jsonl_and_meta_json_mutually_exclusive(self):
        r = run_metalstat("--jsonl", "--meta-json")
        assert r.returncode != 0
        assert "mutually exclusive" in r.stderr

    def test_json_flag_removed(self):
        r = run_metalstat("--json")
        assert r.returncode != 0

    def test_show_all(self):
        r = run_metalstat("-a", "--no-color")
        assert r.returncode == 0
        assert "CPU" in r.stdout
        assert "Memory" in r.stdout
        assert "Power" in r.stdout
        assert "Pkg" in r.stdout

    def test_show_cpu(self):
        r = run_metalstat("-c", "--no-color")
        assert r.returncode == 0
        assert "CPU" in r.stdout

    def test_show_power(self):
        r = run_metalstat("-P", "--no-color")
        assert r.returncode == 0
        assert "W" in r.stdout

    def test_no_header(self):
        r = run_metalstat("--no-header", "--no-color")
        assert r.returncode == 0
        lines = r.stdout.strip().split("\n")
        # First line should be the status line, not hostname
        assert "Apple" in lines[0]

    def test_jsonl_has_cpu_and_power_without_flags(self):
        # --jsonl always queries CPU + GPU + power regardless of display flags,
        # so analysis pipelines get a uniform schema.
        r = run_metalstat("--jsonl")
        assert r.returncode == 0
        data = json.loads(r.stdout.strip().splitlines()[0])
        assert data["cpu_util"] is not None
        assert data["pkg_w"] is not None or data["cpu_w"] is not None
