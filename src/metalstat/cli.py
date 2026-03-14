"""CLI interface for metalstat."""

from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback

from metalstat import __version__
from metalstat.core import AppleSiliconStat, DisplayOptions
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
        "--json",
        action="store_true",
        dest="json_output",
        help="Output in JSON format",
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

    return parser


def _determine_color(args: argparse.Namespace) -> bool:
    """Determine whether to use color output."""
    if args.no_color:
        return False
    if args.color:
        return True
    if args.json_output:
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
        json_output=args.json_output,
    )


def _query_and_print(args: argparse.Namespace, opts: DisplayOptions) -> None:
    """Perform one query and print results."""
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

    if opts.json_output:
        stat.print_json()
    else:
        stat.print_formatted(opts)


def _watch_loop(args: argparse.Namespace, opts: DisplayOptions) -> None:
    """Watch mode using rich Live display."""
    from io import StringIO

    from rich.console import Console
    from rich.live import Live
    from rich.text import Text

    interval = args.interval
    console = Console(
        force_terminal=opts.color if opts.color else None,
        no_color=not opts.color,
        highlight=False,
    )

    stop = False

    def on_sigint(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_sigint)

    needs_gpu = True
    needs_power = opts.show_power or opts.show_ane
    needs_cpu = opts.show_cpu

    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while not stop:
                t0 = time.monotonic()

                stat = AppleSiliconStat.new_query(
                    sample_duration=args.sample_duration,
                    query_cpu=needs_cpu,
                    query_gpu=needs_gpu,
                    query_power=needs_power,
                    query_procs=opts.num_procs if opts.show_procs else 0,
                )

                # Render to a string buffer, then display via Live
                buf = StringIO()
                stat.print_formatted(opts, fp=buf)
                live.update(Text.from_ansi(buf.getvalue()))

                elapsed = time.monotonic() - t0
                remaining = max(0, interval - elapsed)
                deadline = time.monotonic() + remaining
                while time.monotonic() < deadline and not stop:
                    time.sleep(min(0.1, deadline - time.monotonic()))
    except KeyboardInterrupt:
        pass


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

    opts = _make_display_options(args)

    try:
        if args.interval is not None:
            _watch_loop(args, opts)
        else:
            _query_and_print(args, opts)
    except Exception as e:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"metalstat: {e}", file=sys.stderr)
        sys.exit(1)
