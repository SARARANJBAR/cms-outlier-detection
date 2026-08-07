"""Observe what a query actually costs: bytes over the wire, and cold-cache timings.

The app reads its fact tables over HTTP from a GitHub Release asset, so the metric that
decides whether the layout levers matter is **bytes read**, not seconds on a warm local
file. DuckDB's `httpfs` fetches Parquet with HTTP range requests, which means the bytes are
observable — if something is counting them.

`serve_directory` is that something: a local HTTP server that supports range requests and
records every one. Pointing DuckDB at `http://127.0.0.1:<port>/...` then measures the real
read pattern — how many requests, and how many bytes — without publishing anything first,
and with the network removed so the numbers are about layout and nothing else.

`drop_page_cache` is the other half, for timings rather than bytes. On macOS it needs
`sudo purge`, so it reports whether it actually worked instead of quietly returning a warm
number dressed up as a cold one.
"""

from __future__ import annotations

import http.server
import re
import socket
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@dataclass(frozen=True)
class Fetch:
    """One HTTP request the reader made."""

    path: str
    start: int | None
    end: int | None
    bytes_sent: int


@dataclass
class Traffic:
    """What a reader asked for. Reset between measurements."""

    fetches: list[Fetch] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes_sent for f in self.fetches)

    @property
    def n_requests(self) -> int:
        return len(self.fetches)

    def reset(self) -> None:
        self.fetches.clear()

    def summary(self) -> str:
        return f"{self.total_bytes:,} bytes in {self.n_requests} requests"


def _handler_class(root: Path, traffic: Traffic) -> type[http.server.BaseHTTPRequestHandler]:
    class RangeHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # noqa: D102 - silence per-request stderr
            pass

        def _resolve(self) -> Path | None:
            target = (root / self.path.lstrip("/")).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                return None
            return target

        def do_HEAD(self) -> None:
            target = self._resolve()
            if target is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self) -> None:
            target = self._resolve()
            if target is None:
                self.send_error(404)
                return
            size = target.stat().st_size
            match = RANGE_RE.match(self.headers.get("Range", "") or "")

            if match:
                raw_start, raw_end = match.groups()
                if raw_start:
                    start = int(raw_start)
                    end = int(raw_end) if raw_end else size - 1
                else:  # suffix range: the last N bytes, which is how a footer is read
                    start, end = size - int(raw_end), size - 1
                end = min(end, size - 1)
                length = max(0, end - start + 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                start, end, length = 0, size - 1, size
                self.send_response(200)

            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            with target.open("rb") as f:
                f.seek(start)
                self.wfile.write(f.read(length))

            traffic.fetches.append(Fetch(self.path, start, end, length))

    return RangeHandler


@contextmanager
def serve_directory(root: Path | str):
    """Serve ``root`` over HTTP on a free port, counting every byte the reader fetches.

    Yields ``(base_url, traffic)``. Call ``traffic.reset()`` immediately before the query
    being measured, since DuckDB fetches metadata the first time it opens a file.
    """
    root = Path(root)
    traffic = Traffic()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler_class(root, traffic))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", traffic
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def drop_page_cache() -> bool:
    """Try to evict the OS page cache. Returns whether it actually happened.

    A "cold" timing measured without this is just a warm timing with a different label, so
    the caller needs to know it failed rather than being handed a number to believe.
    """
    try:
        subprocess.run(["sync"], check=True, timeout=60)
        subprocess.run(["sudo", "-n", "purge"], check=True, timeout=300, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True
