"""Package entry point — invoked via `python -m ursa_oscar_mcp`.

Importing `mcp` from `.server` ensures the server module loads exactly once
(under the canonical `ursa_oscar_mcp.server` name), so tool modules that do
`from ..server import mcp` register against the same FastMCP instance the
running uvicorn loop is serving.

If we ran `python -m ursa_oscar_mcp.server` instead, Python would create a
second instance of the server module (one as __main__, one as the package
module), and tools would register against the package-module copy while
uvicorn served the __main__ copy — claude.ai's tools/list would return [].
"""
from __future__ import annotations

import logging

import uvicorn

from .server import mcp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = mcp.http_app(transport="sse")
    print(
        "ursa-oscar-mcp: SSE listening on :8000 "
        "(oauth=ready, dcr=DISABLED, pre_registered_client=required, "
        "static_bearer=enabled)"
    )
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
