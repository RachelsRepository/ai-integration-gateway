"""Run Provider B with uvicorn."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=18002, reload=False)
