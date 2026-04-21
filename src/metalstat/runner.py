"""`metalstat run` — wrap a child process and stream metrics during its lifetime.

Writes three sibling files under a `-o PREFIX`:
- `PREFIX.meta.json` : static machine info, captured before the child starts
- `PREFIX.jsonl`     : per-tick sample lines, written while the child runs
- `PREFIX.log`       : child stdout+stderr (merged), only when `--capture` is set

The child inherits stdin and (by default) stdout/stderr, so it behaves exactly
as if invoked directly. SIGINT and SIGTERM delivered to metalstat are forwarded
to the child; metalstat exits with the child's exit code.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

from metalstat.core import AppleSiliconStat


def _write_meta_file(path: Path) -> None:
    stat = AppleSiliconStat.new_query(
        sample_duration=0.0,
        query_cpu=False,
        query_gpu=False,
        query_power=False,
        query_procs=0,
    )
    with path.open("w") as f:
        json.dump(stat.to_meta_dict(), f, indent=2)
        f.write("\n")


def _sampler_thread(
    jsonl_path: Path,
    sample_duration: float,
    interval: float,
    stop_event: threading.Event,
    start_time: float,
) -> None:
    """Append one JSONL sample per `interval` seconds until `stop_event` is set."""
    try:
        with jsonl_path.open("w") as f:
            while not stop_event.is_set():
                t0 = time.monotonic()
                try:
                    stat = AppleSiliconStat.new_query(
                        sample_duration=sample_duration,
                        query_cpu=True,
                        query_gpu=True,
                        query_power=True,
                        query_procs=0,
                    )
                    line = json.dumps(stat.to_sample_dict(start_time=start_time))
                    f.write(line + "\n")
                    f.flush()
                except Exception as e:
                    # Keep sampling — child is more important than any one tick.
                    print(f"metalstat: sampler tick failed: {e}", file=sys.stderr)

                deadline = t0 + interval
                while not stop_event.is_set() and time.monotonic() < deadline:
                    stop_event.wait(timeout=min(0.1, deadline - time.monotonic()))
    except Exception as e:
        print(f"metalstat: sampler crashed: {e}", file=sys.stderr)


def _tee_thread(src: IO[bytes], term_fp: IO[bytes], log_fp: IO[bytes]) -> None:
    """Copy bytes from child stdout to both the terminal and the capture log."""
    try:
        for chunk in iter(lambda: src.read(4096), b""):
            term_fp.write(chunk)
            term_fp.flush()
            log_fp.write(chunk)
            log_fp.flush()
    except Exception as e:
        print(f"metalstat: capture thread error: {e}", file=sys.stderr)


def run_wrapper(
    output_prefix: str,
    interval: float,
    sample_duration: float,
    capture: bool,
    child_argv: list[str],
) -> int:
    if not child_argv:
        print(
            "metalstat run: no command specified — use `-- <cmd> [args...]`",
            file=sys.stderr,
        )
        return 2

    meta_path = Path(f"{output_prefix}.meta.json")
    jsonl_path = Path(f"{output_prefix}.jsonl")
    log_path = Path(f"{output_prefix}.log") if capture else None

    _write_meta_file(meta_path)

    stop_event = threading.Event()
    start_time = time.time()
    sampler = threading.Thread(
        target=_sampler_thread,
        args=(jsonl_path, sample_duration, interval, stop_event, start_time),
        daemon=True,
    )
    sampler.start()

    popen_kwargs: dict = {}
    log_fp: IO[bytes] | None = None
    tee: threading.Thread | None = None

    if capture:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.STDOUT
        popen_kwargs["bufsize"] = 0

    try:
        try:
            proc = subprocess.Popen(child_argv, **popen_kwargs)
        except FileNotFoundError as e:
            print(f"metalstat run: {e}", file=sys.stderr)
            return 127

        if capture:
            assert log_path is not None and proc.stdout is not None
            log_fp = log_path.open("wb")
            tee = threading.Thread(
                target=_tee_thread,
                args=(proc.stdout, sys.stdout.buffer, log_fp),
                daemon=True,
            )
            tee.start()

        def forward(sig, frame):
            if proc.poll() is None:
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    pass

        prev_int = signal.signal(signal.SIGINT, forward)
        prev_term = signal.signal(signal.SIGTERM, forward)

        try:
            proc.wait()
        finally:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)

        if tee is not None:
            tee.join(timeout=2.0)

        return proc.returncode
    finally:
        stop_event.set()
        sampler.join(timeout=interval + sample_duration + 1.0)
        if log_fp is not None:
            log_fp.close()
