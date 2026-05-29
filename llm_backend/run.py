import uvicorn
import os
from pathlib import Path


def start_server():
    os.chdir(Path(__file__).parent)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        access_log=False,
        log_level="error",
        reload=True
    )


if __name__ == "__main__":
    start_server() 