# metalstat

Apple Silicon GPU/CPU/Memory monitoring CLI — like [gpustat](https://github.com/wookayin/gpustat), but for Metal.

```
$ metalstat
ran-mbp.local  2026-03-13 19:22:02
Apple M1 Pro | GPU   8.5%, 388 MHz | 15.5 / 32.0 GB | Pressure: ●green
```

No sudo required. Uses IOReport private API for GPU/power metrics.

## Install

```bash
pip install metalstat

# With Metal GPU memory tracking (optional, requires pyobjc):
pip install metalstat[metal]
```

Or with [uv](https://docs.astral.sh/uv/):
```bash
uv tool install metalstat
```

## Usage

```bash
# Quick glance (GPU utilization, frequency, memory, pressure)
metalstat

# Show everything
metalstat -a

# CPU utilization with P/E cluster breakdown
metalstat -c

# Power consumption (GPU/CPU/Package)
metalstat -P

# Detailed memory breakdown
metalstat -m

# Metal GPU memory allocation
metalstat -g

# JSON output for scripting
metalstat --json

# Watch mode (refresh every 1s)
metalstat -i
metalstat -i 2         # every 2 seconds
metalstat -a -i 1      # all metrics, 1s refresh

# Combine flags
metalstat -cP          # CPU + Power
metalstat -mg          # memory detail + Metal GPU mem
```

## Output examples

**Default:**
```
Apple M1 Pro | GPU   8.5%, 388 MHz | 15.5 / 32.0 GB | Pressure: ●green
```

**`metalstat -a` (all metrics):**
```
ran-mbp.local  2026-03-13 21:04:38
Apple M1 Pro  8C CPU (6P + 2E) / 14C GPU / Apple Family 7
     GPU   10.9%     388 MHz   0.1W
     CPU   45.4%   P:   13%   E:    0%   3.6W
  Memory  15.6 / 32.0 GB   ●green
          2.7G wired / 12.9G active / 12.9G inactive / 2.5G compressed
   Metal  0.0G / 25.0G
    Swap  3.9G / 5.0G
   Power  Pkg 4.8W   CPU 3.6W   GPU 0.1W   DRAM 2.2W
```

**`metalstat --json`:**
```json
{
  "hostname": "ran-mbp.local",
  "chip": {
    "name": "Apple M1 Pro",
    "cpu_cores": { "total": 8, "performance": 6, "efficiency": 2 },
    "gpu_cores": 14,
    "metal_family": "Apple Family 7"
  },
  "memory": {
    "total_gb": 32.0, "used_gb": 15.35, "available_gb": 13.23,
    "wired_gb": 2.85, "active_gb": 12.5, "inactive_gb": 12.12,
    "compressed_gb": 2.62,
    "pressure_percent": 18.0, "pressure_level": "green"
  },
  "gpu": { "utilization": 10.4, "frequency_mhz": 388 },
  "gpu_memory": { "allocated_gb": 0.0, "recommended_max_gb": 24.96 },
  "power": { "cpu_w": 1.75, "gpu_w": 0.04, "package_w": 2.72 }
}
```

## Options

| Flag | Description |
|------|-------------|
| `-c, --show-cpu` | CPU utilization with P/E cluster breakdown |
| `-P, --show-power` | Power consumption (GPU/CPU/DRAM/Package) |
| `-m, --show-memory` | Detailed memory breakdown |
| `-g, --show-gpu-mem` | Metal GPU memory allocation |
| `-s, --show-swap` | Swap usage |
| `--show-ane` | ANE (Neural Engine) power |
| `-a, --show-all` | Enable all display options |
| `--json` | JSON output |
| `--no-color` | Suppress colors |
| `--no-header` | Suppress hostname/timestamp |
| `-i [N], --interval [N]` | Watch mode, refresh every N seconds (default: 1) |
| `--sample-duration N` | IOReport sample window in seconds (default: 0.2) |
| `--debug` | Show stack traces on error |

## Understanding Apple Silicon memory (vs. CUDA)

Apple Silicon uses **Unified Memory Architecture (UMA)** — the CPU and GPU share
a single pool of RAM. There is no separate VRAM. This is fundamentally different
from NVIDIA/CUDA where the GPU has its own dedicated memory (e.g. 24GB VRAM on an
RTX 4090) and data must be copied between CPU and GPU over PCIe.

### What the memory numbers mean

```
  Memory  15.6 / 32.0 GB   ●green                    ← system memory (shared by CPU + GPU)
          2.7G wired / 12.9G active / ...             ← breakdown by page state
   Metal  0.0G / 25.0G                                ← GPU allocation / recommended max
```

**System memory** (`15.6 / 32.0 GB`) is the total unified memory usage — CPU and
GPU workloads combined. The breakdown shows:
- **Wired**: Locked by the kernel, cannot be paged out or compressed
- **Active**: Recently used pages
- **Inactive**: Not recently accessed, still in RAM, reclaimable
- **Compressed**: macOS compresses inactive pages in-memory before swapping to disk

**Metal GPU allocation** (`0.0G / 25.0G`) shows how much memory is currently
allocated for GPU resources (textures, buffers, ML model weights) vs. the
**recommended maximum**. This is the closest equivalent to "VRAM used / VRAM total"
on NVIDIA, but with important differences:

| | NVIDIA (CUDA) | Apple Silicon (Metal) |
|---|---|---|
| GPU memory pool | Dedicated VRAM (fixed) | Shared with CPU (unified) |
| "Total" | Physical VRAM size | `recommendedMaxWorkingSetSize` (~75% of RAM) |
| Hard limit? | Yes — allocation fails at VRAM cap | No — soft limit, but going over causes swap thrashing |
| Zero-copy CPU↔GPU? | No, must `cudaMemcpy` | Yes, CPU and GPU see the same physical pages |

The recommended max (~75% of RAM) is not a hardware limit — Metal will let you
allocate beyond it. But exceeding it forces the OS to compress or swap out other
memory, degrading performance. This is why a 192GB Mac can load LLMs that would
need multiple 80GB A100s: the GPU directly accesses main memory with no copy
overhead, but you're sharing that memory budget with the rest of the system.

**Pressure** (`●green` / `●yellow` / `●red`) shows system-wide memory pressure:
- **Green** (>50% free): Healthy, plenty of headroom
- **Yellow** (25-50% free): Moderate pressure, compression active
- **Red** (<25% free): Heavy pressure, swapping likely

## How it works

| Metric | Data Source | Sudo? |
|--------|-----------|-------|
| GPU utilization & frequency | IOReport `libIOReport.dylib` (private API) | No |
| GPU frequency mapping | IORegistry DVFS table (`sgx` device node) | No |
| CPU/GPU/DRAM/Package power | IOReport "Energy Model" channels | No |
| CPU utilization | psutil (`host_processor_info`) | No |
| System memory | psutil (`vm_statistics64`) | No |
| Memory pressure | `memory_pressure` command | No |
| Metal GPU memory | Metal API via PyObjC | No |
| Chip name & core counts | Metal API + sysctl | No |
| Compressed memory | `vm_stat` command | No |

## Requirements

- macOS on Apple Silicon (M1/M2/M3/M4)
- Python 3.10+

## License

MIT
