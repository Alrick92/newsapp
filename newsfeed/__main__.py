"""Run the server: python -m newsfeed [--host 0.0.0.0] [--port 8000] [--demo]"""

import argparse
import logging
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="newsfeed", description="72-hour news aggregator")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--demo", action="store_true", help="serve bundled sample stories instead of polling feeds")
    args = parser.parse_args()
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
