# metalstat — Design Report

A CLI tool for monitoring Apple Silicon GPU/CPU/Memory status, inspired by [gpustat](https://github.com/wookayin/gpustat).

---

## 1. Motivation & Goals

`gpustat` is a beloved tool for NVIDIA GPU monitoring — one-liner output, colored, process-aware, JSON-capable. There is no equivalent for Apple Silicon. Existing tools either require `sudo` (asitop), are written in Rust/Go/Swift (macmon, mactop, NeoAsitop), or are full TUI dashboards rather than quick CLI one-liners.

**metalstat** fills this gap: a Python CLI that gives you a quick glance at your Mac's GPU, CPU, and memory status in one line — no sudo required.

### Design Principles

1. **Quick glance** — default output is a compact, colored one-liner (like gpustat)
2. **No sudo required** — use IOReport private API + Metal + psutil (avoid powermetrics)
3. **Python-native** — installable via `pip install metalstat`, no compiled extensions
4. **Familiar interface** — CLI flags and output format mirror gpustat where applicable
5. **JSON-capable** — machine-readable output for scripting and monitoring pipelines

---

## 2. Apple Silicon Architecture Background

### 2.1 Unified Memory Architecture (UMA)

Apple Silicon has a single pool of LPDDR memory shared by CPU, GPU, Neural Engine, and media engines. There is no separate VRAM — the GPU reads directly from main memory with zero-copy access.

| Generation | Max RAM  | Memory Bandwidth |
|------------|----------|-------------------|
| M1         | 16 GB    | 68 GB/s           |
| M1 Pro     | 32 GB    | 200 GB/s          |
| M1 Max     | 64 GB    | 400 GB/s          |
| M2         | 24 GB    | 100 GB/s          |
| M3         | 24 GB    | 100 GB/s          |
| M4         | 32 GB    | 120 GB/s          |
| M4 Pro     | 48 GB    | 273 GB/s          |
| M4 Max     | 128 GB   | 546 GB/s          |

### 2.2 macOS Memory Categories

| Category     | Description                                               |
|--------------|-----------------------------------------------------------|
| **Wired**    | Locked in RAM by kernel/drivers. Cannot be paged out or compressed. |
| **Active**   | Recently accessed pages currently in use.                 |
| **Inactive** | Not recently accessed but still in RAM. Reclaimable.      |
| **Compressed** | Inactive pages compressed in-memory (before swap).      |
| **Free**     | Truly unused pages.                                       |
| **Purgeable** | App-marked caches the OS can discard under pressure.     |
| **App Memory** | Memory actively used by applications.                   |

Key formula: `Memory Used = App Memory + Wired + Compressed`

Memory pressure levels: **Green** (>50% free), **Yellow** (25-50%), **Red** (<25%)

### 2.3 GPU Memory on Apple Silicon

Since memory is unified, there's no "VRAM" in the NVIDIA sense. Metal exposes:

- **`MTLDevice.recommendedMaxWorkingSetSize`** — safe GPU allocation limit (~65-75% of total RAM)
- **`MTLDevice.currentAllocatedSize`** — bytes currently allocated for Metal resources
- **`MTLDevice.hasUnifiedMemory`** — always `true` on Apple Silicon

### 2.4 Key Differences from NVIDIA Monitoring

| Aspect | NVIDIA (gpustat) | Apple Silicon (metalstat) |
|--------|------------------|---------------------------|
| Memory model | Separate VRAM | Unified (shared with CPU) |
| GPU utilization | NVML API (no sudo) | IOReport private API (no sudo) |
| Per-process GPU usage | NVML provides per-process VRAM | **Not available** on Apple Silicon |
| Temperature | NVML API | IOHIDEventSystemClient (private) or compiled helper |
| Power draw | NVML API | IOReport "Energy Model" channel |
| Fan speed | NVML API | SMC (not applicable on laptops without fans) |
| Driver version | NVML API | N/A (Metal version instead) |

---

## 3. Feature Specification

### 3.1 Core Metrics

**Tier 1 — Default display (no flags needed):**

| Metric | Source | Sudo? |
|--------|--------|-------|
| Chip name (e.g., "Apple M4 Pro") | Metal API `device.name()` | No |
| GPU utilization % | IOReport "GPU Stats" channel | No |
| GPU frequency (MHz) | IOReport "GPU Stats" channel | No |
| System memory: used / total | psutil `virtual_memory()` | No |
| Memory pressure level | `memory_pressure` or Mach API | No |

**Tier 2 — Opt-in with flags:**

| Metric | Flag | Source | Sudo? |
|--------|------|--------|-------|
| CPU utilization % (per-cluster) | `-c` / `--show-cpu` | psutil or IOReport | No |
| P-core / E-core counts | `-c` / `--show-cpu` | sysctl | No |
| GPU power (Watts) | `-P` / `--show-power` | IOReport "Energy Model" | No |
| CPU power (Watts) | `-P` / `--show-power` | IOReport "Energy Model" | No |
| Package power (Watts) | `-P` / `--show-power` | IOReport "Energy Model" | No |
| Memory breakdown (wired/active/inactive/compressed) | `-m` / `--show-memory` | psutil / vm_stat | No |
| GPU Metal allocation | `-g` / `--show-gpu-mem` | Metal API | No |
| Swap usage | `-s` / `--show-swap` | psutil `swap_memory()` | No |
| Temperature (CPU/GPU die) | `-t` / `--show-temp` | IOHIDEventSystemClient helper | No* |
| ANE power | `--show-ane` | IOReport | No |
| All metrics | `-a` / `--show-all` | All above | No |

*Temperature requires a compiled Objective-C helper binary bundled with the package.

**Tier 3 — Future / stretch:**

| Metric | Notes |
|--------|-------|
| Memory bandwidth (GB/s) | IOReport "DCS" channels, complex |
| Per-process GPU memory | Not available on Apple Silicon |
| Neural Engine utilization | IOReport, limited utility |

### 3.2 Output Format

**Default one-liner:**
```
Apple M4 Pro | GPU  42%, 900 MHz | 12.3 / 18.0 GB | Pressure: ●green
```

**With `--show-cpu`:**
```
Apple M4 Pro | GPU  42%, 900 MHz | CPU  67% (P: 82%, E: 31%) | 12.3 / 18.0 GB | Pressure: ●green
```

**With `--show-power`:**
```
Apple M4 Pro | GPU  42%, 900 MHz, 3.5W | CPU 8.2W | Pkg 15.1W | 12.3 / 18.0 GB | Pressure: ●green
```

**With `--show-memory` (expanded breakdown):**
```
Apple M4 Pro | GPU  42%, 900 MHz | 12.3 / 18.0 GB | Pressure: ●green
  Memory: 5.2G wired, 4.1G active, 1.8G inactive, 1.2G compressed | Metal: 0.8G / 13.5G
```

**With `--show-all`:**
```
myhost  2025-01-15 14:32:01
Apple M4 Pro (10C CPU: 4P+6E, 16C GPU) | GPU  42%, 900 MHz, 3.5W | CPU  67% (P: 82%, E: 31%), 8.2W | 12.3 / 18.0 GB | Pressure: ●green
  Memory: 5.2G wired, 4.1G active, 1.8G inactive, 1.2G compressed | Metal: 0.8G / 13.5G | Swap: 0.0G / 4.0G | ANE: 0.0W | Pkg: 15.1W
```

**JSON format (`--json`):**
```json
{
  "hostname": "myhost",
  "query_time": "2025-01-15T14:32:01",
  "chip": {
    "name": "Apple M4 Pro",
    "cpu_cores": {"performance": 4, "efficiency": 6, "total": 10},
    "gpu_cores": 16
  },
  "gpu": {
    "utilization": 42.31,
    "frequency_mhz": 900,
    "power_w": 3.45
  },
  "cpu": {
    "utilization": 67.2,
    "utilization_p_cluster": 82.1,
    "utilization_e_cluster": 31.4,
    "power_w": 8.2
  },
  "memory": {
    "total_gb": 18.0,
    "used_gb": 12.3,
    "available_gb": 5.7,
    "wired_gb": 5.2,
    "active_gb": 4.1,
    "inactive_gb": 1.8,
    "compressed_gb": 1.2,
    "pressure_percent": 32,
    "pressure_level": "green"
  },
  "gpu_memory": {
    "allocated_gb": 0.8,
    "recommended_max_gb": 13.5
  },
  "swap": {
    "used_gb": 0.0,
    "total_gb": 4.0
  },
  "power": {
    "package_w": 15.1,
    "cpu_w": 8.2,
    "gpu_w": 3.45,
    "ane_w": 0.0
  }
}
```

### 3.3 CLI Interface

```
metalstat [OPTIONS]

Display Options:
  -c, --show-cpu          Show CPU utilization (per-cluster)
  -P, --show-power        Show power consumption (GPU/CPU/Package)
  -m, --show-memory       Show detailed memory breakdown
  -g, --show-gpu-mem      Show Metal GPU memory allocation
  -s, --show-swap         Show swap usage
  -t, --show-temp         Show temperature (requires helper binary)
  --show-ane              Show ANE (Neural Engine) power
  -a, --show-all          Enable all display options

Output Options:
  --json                  Output in JSON format
  --no-color              Suppress colored output
  --color                 Force colored output
  --no-header             Suppress hostname/timestamp header

Watch Mode:
  -i, --interval SECONDS  Watch mode with update interval (default: 1.0)

Other:
  --debug                 Show debug info and stack traces
  -v, --version           Show version
  -h, --help              Show help
```

---

## 4. Architecture

### 4.1 Package Structure

```
metalstat/
├── metalstat/
│   ├── __init__.py          # Package init, exports main API
│   ├── __main__.py          # python -m metalstat support
│   ├── cli.py               # CLI argument parsing, watch loop
│   ├── core.py              # AppleSiliconStat dataclass, formatting
│   ├── gpu.py               # GPU metrics (Metal API, IOReport)
│   ├── cpu.py               # CPU metrics (psutil, sysctl, IOReport)
│   ├── memory.py            # Memory metrics (psutil, vm_stat, Metal)
│   ├── power.py             # Power metrics (IOReport Energy Model)
│   ├── ioreport.py          # IOReport private API bindings (ctypes)
│   ├── thermal.py           # Temperature reading (optional helper)
│   ├── sysinfo.py           # Static system info (chip name, core counts)
│   └── util.py              # Color helpers, formatting, unit conversion
├── pyproject.toml
├── LICENSE
└── README.md
```

### 4.2 Module Responsibilities

```
                    ┌──────────┐
                    │  cli.py  │  Argument parsing, watch loop, entry point
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ core.py  │  AppleSiliconStat: orchestrates queries,
                    │          │  holds all metrics, formats output
                    └────┬─────┘
                         │
          ┌──────┬───────┼────────┬─────────┐
          │      │       │        │         │
     ┌────▼──┐ ┌─▼───┐ ┌▼──────┐ ┌▼──────┐ ┌▼────────┐
     │gpu.py │ │cpu.py│ │mem.py │ │power.py│ │sysinfo.py│
     └───┬───┘ └──┬──┘ └───┬───┘ └───┬───┘ └────┬────┘
         │        │        │         │           │
    ┌────▼────────▼────────▼─────────▼───┐  ┌───▼──────┐
    │         ioreport.py                │  │  sysctl   │
    │   (ctypes bindings to              │  │  Metal    │
    │    libIOReport.dylib)              │  │  psutil   │
    └────────────────────────────────────┘  └──────────┘
```

### 4.3 Data Flow

1. `cli.py` parses arguments, enters single-shot or watch loop
2. `core.py` calls `AppleSiliconStat.new_query()` which:
   a. Calls `sysinfo.get_chip_info()` once (cached) — chip name, core counts
   b. Calls `gpu.get_gpu_metrics()` — IOReport GPU Stats for utilization/frequency
   c. Calls `cpu.get_cpu_metrics()` — psutil + IOReport for per-cluster utilization
   d. Calls `memory.get_memory_metrics()` — psutil + Metal for memory breakdown
   e. Calls `power.get_power_metrics()` — IOReport Energy Model for power data
   f. Optionally calls `thermal.get_temperatures()` — compiled helper
3. `core.py` formats output (text or JSON) and prints to stdout

### 4.4 IOReport Bindings Strategy

The IOReport private API is the key to **sudoless** GPU/CPU/power monitoring. The binding approach:

```python
# ioreport.py — ctypes wrapper for libIOReport.dylib

import ctypes
from ctypes import c_void_p

# Load frameworks
_iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
_ioreport = ctypes.cdll.LoadLibrary("/usr/lib/libIOReport.dylib")
_cf = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)

# Key functions to bind:
# 1. IOReportCopyChannelsInGroup(group_name, subgroup_name) -> CFDictionary
# 2. IOReportMergeChannels(dict1, dict2, nil) -> merged CFDictionary
# 3. IOReportCreateSubscription(nil, channels, ...) -> subscription
# 4. IOReportCreateSamples(subscription, ...) -> sample CFDictionary
# 5. IOReportCreateSamplesDelta(sample1, sample2, nil) -> delta CFDictionary

# Channel groups needed:
# - "GPU Stats"     → GPU active residency, frequency
# - "Energy Model"  → CPU/GPU/ANE/Package power
# - "CPU Stats"     → Per-core/cluster CPU metrics (optional)
```

**Sampling workflow:**
1. On init: create subscription for desired channel groups
2. On each query: take sample₁, sleep for interval, take sample₂
3. Compute delta between samples → utilization and power values
4. Parse the delta CFDictionary to extract values

**ARM64 ctypes caveat:** Must explicitly set `argtypes` for all ctypes function bindings on Apple Silicon to avoid calling convention issues with variadic functions.

### 4.5 Graceful Degradation

Not all metrics are available on all systems. metalstat should degrade gracefully:

| Scenario | Behavior |
|----------|----------|
| Not Apple Silicon (Intel Mac) | Error: "metalstat requires Apple Silicon" |
| IOReport unavailable | Fall back to powermetrics (with sudo warning) or show "??" |
| Metal framework unavailable | Skip GPU memory allocation metrics |
| Temperature helper not built | Skip temperature, show "??" with `--show-temp` |
| psutil not installed | Error on import (it's a required dependency) |
| Running in SSH session | Auto-disable colors (detect tty) |

---

## 5. Key Implementation Details

### 5.1 IOReport Channel Parsing

The IOReport delta samples return CFDictionary trees. Extracting values requires walking the tree:

```python
# Pseudocode for GPU utilization from IOReport delta
def parse_gpu_stats(delta):
    # IOReportIterateOverChannelItems iterates over channels
    # Each channel has: group, subgroup, channel_name, and value(s)
    # For "GPU Stats" group:
    #   - "GPU Active" residency channels give utilization
    #   - Frequency state residency gives current frequency

    gpu_active = 0.0
    for channel in iterate_channels(delta, group="GPU Stats"):
        if "GPU Active" in channel.name:
            gpu_active = channel.value  # fraction 0.0-1.0
    return gpu_active * 100  # percentage
```

### 5.2 Color Scheme

Following gpustat's approach — conditional colors based on severity:

| Metric | Low (green) | Medium (yellow) | High (red) |
|--------|-------------|-----------------|------------|
| GPU utilization | < 30% | 30-80% | > 80% |
| CPU utilization | < 50% | 50-85% | > 85% |
| Memory pressure | Green | Yellow | Red |
| Temperature | < 60°C | 60-85°C | > 85°C |
| Power | < 33% TDP | 33-66% TDP | > 66% TDP |

Use the `blessed` library (same as gpustat) for terminal colors.

### 5.3 Watch Mode

```python
# cli.py watch loop (simplified)
def watch_loop(interval, options):
    term = blessed.Terminal()
    with term.fullscreen(), term.cbreak(), term.hidden_cursor():
        while True:
            stat = AppleSiliconStat.new_query()
            print(term.home + term.clear)
            stat.print_formatted(options)
            time.sleep(interval)
```

### 5.4 Caching

- **Static info** (chip name, core counts, Metal device properties): query once, cache for session
- **IOReport subscription**: create once, reuse across queries
- **IOReport samples**: keep previous sample to compute delta on next query

---

## 6. Dependencies

| Package | Purpose | Required? |
|---------|---------|-----------|
| `psutil >= 5.9.0` | CPU/memory/swap/process metrics | Yes |
| `blessed >= 1.17.1` | Terminal colors and fullscreen | Yes |
| `pyobjc-framework-Metal` | Metal device info, GPU memory | Yes |
| `pyobjc-core` | Foundation types for ctypes interop | Yes |

No `nvidia-ml-py` equivalent needed — we use ctypes + IOReport directly.

### Python Version

Python 3.9+ (matching macOS system Python on modern macOS).

---

## 7. Comparison with Existing Tools

| Feature | metalstat | asitop | macmon | mactop | gpustat |
|---------|-----------|--------|--------|--------|---------|
| Language | Python | Python | Rust | Go | Python |
| Sudo required | **No** | Yes | No | No | No |
| Install | pip | pip | brew | brew | pip |
| Output style | One-liner | Full TUI | TUI | TUI | One-liner |
| JSON output | Yes | No | No | No | Yes |
| Watch mode | Yes | Yes | Yes | Yes | Yes |
| GPU util | Yes | Yes | Yes | Yes | Yes |
| CPU util | Yes | Yes | Yes | Yes | N/A |
| Memory detail | Yes | Basic | Basic | Basic | VRAM only |
| Power metrics | Yes | Yes | Yes | Yes | Optional |
| Temperature | Optional | No | Yes | No | Yes |
| Per-process GPU | No* | No* | No* | No* | Yes |
| Scriptable | Yes | No | No | No | Yes |

*Per-process GPU utilization is not available on Apple Silicon at the OS level.

**metalstat's niche:** The only Python, pip-installable, no-sudo, one-liner CLI for Apple Silicon monitoring with JSON output. It combines gpustat's UX with macmon's sudoless IOReport approach.

---

## 8. Implementation Phases

### Phase 1 — MVP (Core metrics, no IOReport)
- System info via sysctl + Metal API
- Memory via psutil
- GPU memory via Metal API
- Basic CLI with `--json`, `--no-color`, watch mode
- Output: `Apple M4 Pro | 12.3 / 18.0 GB | Metal: 0.8G / 13.5G | Pressure: ●green`

### Phase 2 — IOReport Integration (GPU utilization + power)
- Bind IOReport via ctypes
- GPU utilization and frequency from "GPU Stats"
- Power metrics from "Energy Model"
- CPU utilization from psutil (per-core)
- Full one-liner output with all Tier 1 metrics

### Phase 3 — Polish
- CPU per-cluster utilization via IOReport "CPU Stats"
- Temperature via compiled helper (optional)
- Shell completions
- Comprehensive error handling and graceful degradation
- PyPI release

---

## 9. Open Questions & Risks

| Question | Impact | Mitigation |
|----------|--------|------------|
| IOReport API stability — it's private and undocumented | Could break across macOS versions | Pin supported macOS versions, test on each release. macmon/mactop have been stable for 2+ years. |
| ARM64 ctypes calling conventions | Crashes if argtypes not set correctly | Thorough testing, reference macmon/SocPowerBuddy C code |
| Temperature sensor keys vary per SoC | Helper binary needs updating per chip gen | Ship pre-compiled binaries or make temp optional |
| pyobjc-framework-Metal install size | ~50MB for full pyobjc | Only require pyobjc-framework-Metal + pyobjc-core |
| Per-process GPU usage unavailable | Users may expect it (coming from gpustat) | Document clearly, show system-wide GPU allocation instead |
| IOReport sample timing | Need ≥100ms between samples for accuracy | Default 200ms sample window, configurable |

---

## 10. References

- **gpustat**: https://github.com/wookayin/gpustat — inspiration for CLI design
- **macmon**: https://github.com/vladkens/macmon — IOReport bindings reference (Rust)
- **SocPowerBuddy**: https://github.com/dehydratedpotato/socpowerbud — IOReport reverse engineering
- **asitop**: https://github.com/tlkh/asitop — Python Apple Silicon monitor (sudo-based)
- **NeoAsitop**: https://github.com/op06072/NeoAsitop — sudoless Swift rewrite
- **mactop**: https://github.com/metaspartan/mactop — Go-based monitor
- **apple_sensors**: https://github.com/fermion-star/apple_sensors — temperature helper
- **Apple Metal Docs**: https://developer.apple.com/documentation/metal/mtldevice
- **IOReport decompile**: https://github.com/dehydratedpotato/IOReport_decompile
