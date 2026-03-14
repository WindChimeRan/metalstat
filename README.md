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
ran-mbp.local  2026-03-13 19:38:17
Apple M1 Pro (8C CPU: 6P+2E, 14C GPU) | GPU  15.4%, 388 MHz, 0.1W | CPU  53.4% (P: 65%, E: 25%), 5.9W | 13.9 / 32.0 GB | Pressure: ●green
  Memory: 2.7G wired, 11.2G active, 10.8G inactive, 2.5G compressed | Metal: 0.0G / 25.0G | Swap: 3.9G / 5.0G | Pkg: 7.1W
```

**`metalstat --json`:**
```json
{
  "hostname": "ran-mbp.local",
  "query_time": "2026-03-13T19:22:07.978386",
  "chip": {
    "name": "Apple M1 Pro",
    "cpu_cores": { "total": 8, "performance": 6, "efficiency": 2 },
    "gpu_cores": 14,
    "metal_family": "Metal 2, Apple Family 7"
  },
  "memory": {
    "total_gb": 32.0, "used_gb": 15.35, "available_gb": 13.23,
    "wired_gb": 2.85, "active_gb": 12.5, "inactive_gb": 12.12,
    "compressed_gb": 2.62,
    "pressure_percent": 18.0, "pressure_level": "green"
  },
  "gpu": { "utilization": 10.4, "frequency_mhz": 388 },
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
