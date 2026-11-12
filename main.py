from __future__ import annotations

import argparse

import uvicorn

from web.app import app


def main() -> None:
    parser = argparse.ArgumentParser(description="FM-1 MIDI Bridge")
    parser.add_argument("--host", default="0.0.0.0", help="Web dashboard bind address")
    parser.add_argument("--port", type=int, default=8000, help="Web dashboard port")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
