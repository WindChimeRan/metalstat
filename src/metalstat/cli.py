"""CLI interface for metalstat."""

from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback
from typing import Callable

from metalstat import __version__
from metalstat.core import AppleSiliconStat, DisplayOptions, meta_json, sample_json
from metalstat.sysinfo import is_apple_silicon


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metalstat",
        description="Apple Silicon GPU/CPU/Memory monitoring CLI",
    )

    # Display options
    display = parser.add_argument_group("display options")
    display.add_argument(
        "-c", "--show-cpu",
        action="store_true",
        help="Show CPU utilization (per-cluster breakdown)",
    )
    display.add_argument(
        "-P", "--show-power",
        action="store_true",
        help="Show power consumption (GPU/CPU/Package)",
    )
    display.add_argument(
        "-m", "--show-memory",
        action="store_true",
        help="Show detailed memory breakdown (wired/active/inactive/compressed)",
    )
    display.add_argument(
        "-g", "--show-gpu-mem",
        action="store_true",
        help="Show Metal GPU memory allocation",
    )
    display.add_argument(
        "-s", "--show-swap",
        action="store_true",
        help="Show swap usage",
    )
    display.add_argument(
        "--show-ane",
        action="store_true",
        help="Show ANE (Neural Engine) power",
    )
    display.add_argument(
        "-p", "--show-procs",
        action="store_true",
        help="Show top memory-consuming processes",
    )
    display.add_argument(
        "-n", "--num-procs",
        type=int,
        default=8,
        metavar="N",
        help="Number of top processes to show (default: 8)",
    )
    display.add_argument(
        "-a", "--show-all",
        action="store_true",
        help="Enable all display options",
    )

    # Output options
    output = parser.add_argument_group("output options")
    output.add_argument(
        "--jsonl",
        action="store_true",
        help=(
            "Emit JSON Lines samples to stdout (one line per -i tick, or a "
            "single line and exit when -i is omitted)"
        ),
    )
    output.add_argument(
        "--meta-json",
        action="store_true",
        dest="meta_json",
        help="Emit static system info as a single JSON object and exit",
    )
    # Removed in 0.1.4 — registered here so prefix-matching doesn't silently
    # route `--json` to `--jsonl`, and so users get a useful migration message.
    output.add_argument(
        "--json",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    output.add_argument(
        "--no-color",
        action="store_true",
        help="Suppress colored output",
    )
    output.add_argument(
        "--color", "--force-color",
        action="store_true",
        help="Force colored output",
    )
    output.add_argument(
        "--no-header",
        action="store_true",
        help="Suppress hostname/timestamp header",
    )

    # Watch mode
    parser.add_argument(
        "-i", "--interval", "--watch",
        type=float,
        default=None,
        nargs="?",
        const=1.0,
        metavar="SECONDS",
        help="Watch mode: refresh every N seconds (default: 1.0)",
    )

    # Sampling
    parser.add_argument(
        "--sample-duration",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="IOReport sample duration for GPU/power metrics (default: 0.2)",
    )

    # Other
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show debug information and stack traces",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"metalstat {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand")
    run_parser = subparsers.add_parser(
        "run",
        help="Wrap a command and log system metrics for its lifetime",
        description=(
            "Wrap a child command and log system metrics while it runs. "
            "Produces PREFIX.meta.json (static info) and PREFIX.jsonl "
            "(per-tick samples) under `-o PREFIX`. Add --capture to also "
            "archive the child's stdout+stderr to PREFIX.log."
        ),
    )
    run_parser.add_argument(
        "-o", "--output",
        required=True,
        metavar="PREFIX",
        help="Output file prefix. Produces PREFIX.meta.json, PREFIX.jsonl, and (with --capture) PREFIX.log.",
    )
    run_parser.add_argument(
        "-i", "--interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Sampling interval in seconds (default: 1.0)",
    )
    run_parser.add_argument(
        "--sample-duration",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="IOReport sample window per tick (default: 0.2)",
    )
    run_parser.add_argument(
        "--capture",
        action="store_true",
        help="Also archive the child's stdout+stderr to PREFIX.log (terminal view preserved)",
    )
    run_parser.add_argument(
        "child_argv",
        nargs=argparse.REMAINDER,
        help="Command to run after `--`: e.g. `-- llama-cli -m model.gguf`",
    )

    return parser


def _determine_color(args: argparse.Namespace) -> bool:
    """Determine whether to use color output."""
    if args.no_color:
        return False
    if args.color:
        return True
    if args.jsonl or args.meta_json:
        return False
    return sys.stdout.isatty()


def _make_display_options(args: argparse.Namespace) -> DisplayOptions:
    show_all = args.show_all
    return DisplayOptions(
        show_cpu=show_all or args.show_cpu,
        show_power=show_all or args.show_power,
        show_memory_detail=show_all or args.show_memory,
        show_gpu_mem=show_all or args.show_gpu_mem,
        show_swap=show_all or args.show_swap,
        show_ane=show_all or args.show_ane,
        show_procs=show_all or args.show_procs,
        num_procs=args.num_procs,
        color=_determine_color(args),
        header=not args.no_header,
    )


def _query_and_print(args: argparse.Namespace, opts: DisplayOptions) -> None:
    """Perform one query and print formatted output."""
    needs_gpu = True
    needs_power = opts.show_power or opts.show_ane
    needs_cpu = opts.show_cpu

    stat = AppleSiliconStat.new_query(
        sample_duration=args.sample_duration,
        query_cpu=needs_cpu,
        query_gpu=needs_gpu,
        query_power=needs_power,
        query_procs=opts.num_procs if opts.show_procs else 0,
    )
    stat.print_formatted(opts)


def _emit_meta_json() -> None:
    print(meta_json())


def _emit_sample_line(
    sample_duration: float, start_time: float | None = None
) -> None:
    sys.stdout.write(sample_json(sample_duration, start_time) + "\n")
    sys.stdout.flush()


def _run_interval_loop(interval: float, tick: Callable[[], None]) -> None:
    """Call `tick` every `interval` seconds until SIGINT.

    Sleeps in ~100ms slices so SIGINT is handled promptly.
    """
    stop = False

    def on_sigint(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_sigint)

    try:
        while not stop:
            t0 = time.monotonic()
            tick()
            deadline = t0 + interval
            while time.monotonic() < deadline and not stop:
                time.sleep(min(0.1, deadline - time.monotonic()))
    except KeyboardInterrupt:
        pass


def _jsonl_stream(args: argparse.Namespace) -> None:
    """Stream JSONL samples to stdout at the given interval."""
    start_time = time.time()
    _run_interval_loop(
        args.interval,
        lambda: _emit_sample_line(args.sample_duration, start_time=start_time),
    )


def _watch_loop(args: argparse.Namespace, opts: DisplayOptions) -> None:
    """Watch mode using rich Live display."""
    from io import StringIO

    from rich.console import Console
    from rich.live import Live
    from rich.text import Text

    console = Console(
        force_terminal=opts.color if opts.color else None,
        no_color=not opts.color,
        highlight=False,
    )

    needs_power = opts.show_power or opts.show_ane
    needs_cpu = opts.show_cpu

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        def tick() -> None:
            stat = AppleSiliconStat.new_query(
                sample_duration=args.sample_duration,
                query_cpu=needs_cpu,
                query_gpu=True,
                query_power=needs_power,
                query_procs=opts.num_procs if opts.show_procs else 0,
            )
            buf = StringIO()
            stat.print_formatted(opts, fp=buf)
            live.update(Text.from_ansi(buf.getvalue()))

        _run_interval_loop(args.interval, tick)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not is_apple_silicon():
        print(
            "metalstat: This tool requires Apple Silicon (arm64 macOS).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Handle SIGPIPE
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    if args.subcommand == "run":
        from metalstat.runner import run_wrapper

        child_argv = list(args.child_argv)
        if child_argv and child_argv[0] == "--":
            child_argv = child_argv[1:]
        sys.exit(run_wrapper(
            output_prefix=args.output,
            interval=args.interval,
            sample_duration=args.sample_duration,
            capture=args.capture,
            child_argv=child_argv,
        ))

    if args.json:
        parser.error(
            "--json was removed in 0.1.4. Use --jsonl for per-sample streaming "
            "or --meta-json for one-shot static system info."
        )

    if args.jsonl and args.meta_json:
        parser.error("--jsonl and --meta-json are mutually exclusive")

    opts = _make_display_options(args)

    try:
        if args.meta_json:
            _emit_meta_json()
        elif args.jsonl:
            if args.interval is not None:
                _jsonl_stream(args)
            else:
                _emit_sample_line(sample_duration=args.sample_duration)
        elif args.interval is not None:
            _watch_loop(args, opts)
        else:
            _query_and_print(args, opts)
    except Exception as e:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"metalstat: {e}", file=sys.stderr)
        sys.exit(1)
