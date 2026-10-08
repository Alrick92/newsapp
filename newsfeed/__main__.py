"""Command line.

    python -m newsfeed [--host 0.0.0.0] [--port 8000] [--demo]   run the server
    python -m newsfeed export-opml [FILE]                        write the feed list as OPML
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn


def export_opml(output: str | None) -> None:
    from .feeds import DEFAULT, default_opml_path
    from .opml import read_opml, write_opml

    path = default_opml_path(Path(os.environ.get("NEWSFEED_DATA_DIR", "data")))
    catalog = read_opml(path.read_bytes()) if path.exists() else DEFAULT
    text = write_opml(catalog)
    if output and output != "-":
        Path(output).write_text(text)
        print(f"wrote {len(catalog.feeds)} feeds to {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def main() -> None:
    parser = argparse.ArgumentParser(prog="newsfeed", description="News reader and aggregator")
    parser.add_argument("command", nargs="?", choices=["serve", "export-opml"], default="serve")
    parser.add_argument("output", nargs="?", help="export-opml: file to write (default: stdout)")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--demo", action="store_true", help="serve bundled sample stories instead of polling feeds")
    args = parser.parse_args()

    if args.command == "export-opml":
        export_opml(args.output)
        return
    if args.demo:
        os.environ["NEWSFEED_DEMO"] = "1"

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import create_app
    # Behind a load balancer: trust X-Forwarded-For/Proto from the addresses in
    # FORWARDED_ALLOW_IPS (uvicorn's default is 127.0.0.1 only).
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


if __name__ == "__main__":
    main()
