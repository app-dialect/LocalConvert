"""Entry point.

    python -m localconvert                      open the app
    python -m localconvert --widget             open just the click-wheel widget
    python -m localconvert convert FILE... --to pdf [--out DIR] [--combine]
    python -m localconvert formats [EXT]        list supported conversions
    python -m localconvert doctor               show which engines are installed
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, __version__


def _gui(args) -> int:
    from .server import LocalServer

    server = LocalServer(port=args.port).start()
    state = server.state
    print(f"{APP_NAME} {__version__} is running on this computer only: {server.url}", flush=True)
    print("Files never leave your machine. Close the window (or press Ctrl+C) to quit.", flush=True)

    use_window = not args.browser
    if use_window:
        try:
            import webview  # pywebview: optional native window
        except ImportError:
            use_window = False
    try:
        if use_window:
            try:
                _native_windows(webview, server, "widget" if args.widget else "main")
                return 0
            except Exception as e:  # e.g. no WebView2 runtime on an old Windows install
                print(f"Native window unavailable ({e}); using the web browser instead.", flush=True)
        import webbrowser
        if not args.no_open:
            webbrowser.open(server.page_url("widget" if args.widget else "main"))
        idle_limit = args.idle_minutes * 60
        while not state.shutdown_event.wait(2):
            # In browser mode, exit once the page has been closed for a while and nothing is running.
            if idle_limit and time.time() - state.last_seen > idle_limit and not state.jobs.busy():
                print("No open window for a while - shutting down.", flush=True)
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


class WindowShell:
    """Creates and tracks the native windows: the main app and the floating click-wheel widget."""

    WHEEL_SIZE = 320

    def __init__(self, webview, server):
        self.webview = webview
        self.server = server
        self.windows = {}
        self.lock = threading.Lock()

    def open(self, name: str) -> None:
        with self.lock:
            existing = self.windows.get(name)
            if existing is not None:
                try:
                    existing.show()
                    existing.restore()
                    return
                except Exception:
                    self.windows.pop(name, None)
            url = self.server.page_url(name, native=True)
            if name == "widget":
                window = self.webview.create_window(
                    f"{APP_NAME} Wheel", url, width=self.WHEEL_SIZE, height=self.WHEEL_SIZE,
                    resizable=False, frameless=True, easy_drag=False, transparent=True, on_top=True,
                    **self._corner_position())
            else:
                window = self.webview.create_window(APP_NAME, url, width=1100, height=820, min_size=(720, 560))
            window.events.closed += lambda: self.windows.pop(name, None)
            self.windows[name] = window

    def close(self, name: str) -> None:
        window = self.windows.get(name)
        if window is not None:
            window.destroy()

    def close_all(self) -> None:
        for name in list(self.windows):
            self.close(name)

    def _corner_position(self) -> dict:
        """Park the wheel near the bottom-right corner of the main screen."""
        try:
            screen = self.webview.screens[0]
            return {"x": screen.width - self.WHEEL_SIZE - 40, "y": screen.height - self.WHEEL_SIZE - 90}
        except Exception:
            return {}


def _native_windows(webview, server, first: str) -> None:
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass
    shell = WindowShell(webview, server)
    server.state.shell = shell
    shell.open(first)
    # The in-app Quit button closes every window, which ends webview.start().
    threading.Thread(target=lambda: (server.state.shutdown_event.wait(), shell.close_all()), daemon=True).start()
    webview.start()  # blocks until the last window is closed


def _convert(args) -> int:
    import localconvert.converters  # noqa: F401
    from .core.jobs import combine_to_pdf, convert_one
    from .core.registry import Options
    from .core.util import ConversionError, default_output_dir

    target = args.to.lower().lstrip(".")
    from .core.util import ALIASES
    target = ALIASES.get(target, target)
    opts = Options.from_dict({"quality": args.quality, "dpi": args.dpi, "page_size": args.page_size,
                              "keep_metadata": args.keep_metadata})
    out = Path(args.out).expanduser() if args.out else default_output_dir()
    files = [Path(f) for f in args.files]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("Not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2
    if args.combine:
        if target != "pdf":
            print("--combine only works with --to pdf", file=sys.stderr)
            return 2
        try:
            print(combine_to_pdf(files, out, opts, name=args.name or "Combined"))
            return 0
        except ConversionError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    failed = 0
    for f in files:
        try:
            for p in convert_one(f, target, out, opts):
                print(p)
        except ConversionError as e:
            failed += 1
            print(f"error: {f.name}: {e}", file=sys.stderr)
    return 1 if failed else 0


def _formats(args) -> int:
    import localconvert.converters  # noqa: F401
    from .core import registry
    sources = [args.ext.lower().lstrip(".")] if args.ext else registry.all_sources()
    for src in sources:
        targets = registry.targets_for(src)
        if not targets:
            print(f"{src}: not supported")
            continue
        parts = []
        for t, info in targets.items():
            mark = "" if info["available"] else f" (needs {info['engine']})"
            parts.append(t + ("*" if info["basic"] else "") + mark)
        print(f"{src:8} -> " + ", ".join(parts))
    print("\n* = content is kept but the exact layout may differ")
    return 0


def _doctor(args) -> int:
    from .core import engines
    for name, info in engines.status().items():
        mark = "OK " if info["available"] else "-- "
        print(f"[{mark}] {info['description']}" + (f"\n        {info['path']}" if info.get("path") else ""))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="localconvert", description=f"{APP_NAME}: offline file converter.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--browser", action="store_true", help="use the web browser instead of a native window")
    parser.add_argument("--widget", action="store_true", help="start with just the floating click-wheel widget")
    parser.add_argument("--no-open", action="store_true", help="don't open a browser automatically")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: random free port)")
    parser.add_argument("--idle-minutes", type=int, default=30,
                        help="browser mode: quit after the page has been closed this long (0 = never)")
    sub = parser.add_subparsers(dest="command")

    c = sub.add_parser("convert", help="convert files from the command line")
    c.add_argument("files", nargs="+")
    c.add_argument("--to", required=True, help="target format, e.g. pdf, png, docx, mp3")
    c.add_argument("--out", "-o", help="output folder (default: ~/Downloads/LocalConvert)")
    c.add_argument("--combine", action="store_true", help="merge all inputs into one PDF")
    c.add_argument("--name", help="file name for --combine (without .pdf)")
    c.add_argument("--quality", type=int, default=90)
    c.add_argument("--dpi", type=int, default=150)
    c.add_argument("--page-size", default="A4", choices=["A4", "Letter", "image"])
    c.add_argument("--keep-metadata", action="store_true", help="keep photo EXIF data (camera, GPS, ...)")
    f = sub.add_parser("formats", help="list supported conversions")
    f.add_argument("ext", nargs="?")
    sub.add_parser("doctor", help="show installed conversion engines")

    args = parser.parse_args(argv)
    if args.command == "convert":
        return _convert(args)
    if args.command == "formats":
        return _formats(args)
    if args.command == "doctor":
        return _doctor(args)
    import localconvert.converters  # noqa: F401
    return _gui(args)


if __name__ == "__main__":
    sys.exit(main())
