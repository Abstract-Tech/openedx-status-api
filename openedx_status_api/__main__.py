import logging
import os

import uvicorn

from .app import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host=os.environ.get("STATUS_HOST", "0.0.0.0"), port=int(os.environ.get("STATUS_PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()
