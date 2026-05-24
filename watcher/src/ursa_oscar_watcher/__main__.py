"""Entrypoint — ``python -m ursa_oscar_watcher``.

Wire up logging + load env config + start the watcher loop. The
container CMD points here.
"""
from __future__ import annotations

import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

from .config import WatcherConfig
from .watcher import Watcher


logger = logging.getLogger(__name__)


def _write_version_file(data_dir: Path) -> None:
    """1.1.3 — publish this container's packaged version to a file in
    the shared /data volume. The API container reads this when the
    Settings page asks for the watcher chip's image-version display,
    so the operator no longer has to keep image tags and display env
    vars in sync.

    Best-effort: log on failure, do not block the watcher from
    starting. The chip just shows 'unknown' if the file can't be
    written.
    """
    try:
        v = _pkg_version("ursa-oscar-watcher")
    except PackageNotFoundError:
        v = "dev"
    target = data_dir / "versions" / "watcher.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(v, encoding="utf-8")
        logger.info("Wrote watcher version %s to %s", v, target)
    except OSError as e:
        logger.warning(
            "Could not write version file at %s: %s. Settings page chip "
            "will show 'unknown' for the watcher. This is non-fatal.",
            target, e,
        )


def main() -> int:
    level = os.environ.get("URSA_OSCAR_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    config = WatcherConfig.from_env()
    # The watcher's data volume is bound at /data in the container. The
    # watcher itself doesn't have a config field for this path because
    # it never writes to /data outside of this version-publish helper;
    # the path is canonical and matches the API/MCP containers.
    _write_version_file(Path("/data"))
    Watcher(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
