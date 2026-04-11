"""Allow running as `python -m neuromancy`."""

import os
import uvicorn

from neuromancy.server.app import app

if __name__ == "__main__":
    port = int(os.environ.get("NEUROMANCY_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
