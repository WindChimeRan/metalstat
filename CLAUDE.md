# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Platform

This project runs **only on Apple Silicon** (`arm64` macOS). `cli.py` hard-errors on other platforms, and tests depend on live IOReport samples — they cannot run on Intel Macs, Linux, or CI runners without Apple Silicon.

## Commands

```bash
# Install in editable mode (uv preferred)
uv sync --extra dev

# Run the CLI locally (editable install)
uv run metalstat -a           # all display flags
uv run metalstat --jsonl -i 1 # streaming sample logging
uv run metalstat --meta-json  # static system info, JSON

# Tests (dev extras must be active; they hit real IOReport)
uv run --extra dev python -m pytest tests/ -q
uv run --extra dev python -m pytest tests/test_metalstat.py::TestCore::test_to_sample_dict_flat_and_all_fields -v  # single test

# Build wheel / publish (standard hatchling; keep __version__ in src/metalstat/__init__.py and pyproject.toml in sync)
uv build
```

## Architecture

`AppleSiliconStat.new_query()` in `src/metalstat/core.py` is the single entry point for a snapshot. It orchestrates per-subsystem collectors (`sysinfo`, `memory`, `cpu`, `gpu`, `power`, `procs`), each of which hides its own data source behind a dataclass. Boolean `query_*` flags control which subsystems are polled so callers can skip expensive work (IOReport sampling, `ps` shell-outs).

All per-subsystem IOReport access goes through `ioreport.IOReportSampler`. `core._sampler_cache` (module-level dict keyed by sorted channel-group names) keeps `IOReportSampler` instances alive across calls — do not recreate samplers per tick, the delta calculation and CF object lifetimes depend on reuse. Each `sample_delta(interval)` call takes two IOReport samples `interval` seconds apart, which is the dominant cost of a query (~200ms default).

`cli.py` dispatches to exactly one of four paths (see `main()`): `--meta-json` one-shot → `--jsonl` (one-shot or streaming) → formatted watch (`-i`) → formatted one-shot. `_run_interval_loop(interval, tick)` is the shared cadence + SIGINT-handler helper used by both streaming paths; do not add a third bespoke loop.

Output shapes are deliberately different by mode:
- `stat.print_formatted(opts, fp)` writes the rich-styled human view (color controlled by `DisplayOptions.color`; `_determine_color` decides).
- `stat.to_sample_dict(start_time=None)` returns a **flat** dict for JSONL streaming — one fixed schema with `None` for unavailable fields. Callers pass `start_time` (unix seconds) to seed `elapsed_s`; default makes a standalone sample self-describing with `elapsed_s == 0.0`.
- `stat.to_meta_dict()` returns a nested dict for static per-machine info (hostname, chip, totals) — separate from samples so logs don't repeat hardware fields on every line.

Graceful degradation is built into the type signatures: `AppleSiliconStat.cpu|gpu|power|top_procs` are `None` when not queried; fields inside those dataclasses are individually `None` when a source failed. `to_sample_dict` uses `util.round_or_none` for the `round(x, N) if x is not None else None` pattern — reuse it when adding new fields.

## Relationship to DESIGN.md

`DESIGN.md` is the long-form architecture + IOReport reverse-engineering reference. Read it for:
- CoreFoundation / ctypes / IOReport bindings strategy (§4.4, §5.1)
- P-state residency → GPU utilization derivation (§5.1)
- Energy-channel unit conversion (mJ/nJ → W) caveats
- Tool-comparison rationale (§7)

**DESIGN.md is partially stale** — it still references `--json` (removed in 0.1.4; now `--jsonl` + `--meta-json`) and `blessed` (replaced by `rich`). Treat it as background, not as the current interface spec; the code and `README.md` are authoritative.

## Testing notes

Tests in `tests/test_metalstat.py` invoke the real CLI via `subprocess.run([python, "-m", "metalstat", ...])` and call private collector functions directly. Many assertions depend on the current machine having GPU activity (>0% util) and memory in use — they are integration tests, not pure unit tests. A failure like "utilization is None" typically means IOReport returned no data, not a logic bug.
