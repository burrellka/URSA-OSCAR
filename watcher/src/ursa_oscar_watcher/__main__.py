"""Entrypoint — ``python -m ursa_oscar_watcher``.

Wire up logging + load env config + start the watcher loop. The
container CMD points here.
"""
from __future__ import annotations

import logging
import os
import sys

from .config import WatcherConfig
from .watcher import Watcher


def main() -> int:
    level = os.environ.get("URSA_OSCAR_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    config = WatcherConfig.from_env()
    Watcher(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
