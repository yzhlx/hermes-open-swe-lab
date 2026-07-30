"""Run the Hermes workbench on its configured loopback address."""
from __future__ import annotations

import os
from pathlib import Path

from .runtime import create_server_from_config, load_config


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(os.environ, repo_root)
    server = create_server_from_config(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
