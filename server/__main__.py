from __future__ import annotations

import argparse

from dotenv import load_dotenv

from server.config import load_config


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="server")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind")
    args = parser.parse_args()

    config = load_config()
    port = args.port if args.port is not None else config.port
    host = args.host

    import uvicorn

    from server.app import create_app

    app = create_app(config)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
