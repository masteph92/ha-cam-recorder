import asyncio
import logging
import os
import sys
from pathlib import Path

from .app import App
from .config import ConfigError, load
from .state import StateDB


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        cfg = load(data / "options.json")
    except ConfigError as e:
        logging.error("config: %s", e)
        return 1
    if not cfg.cameras:
        logging.error("no cameras configured")
        return 1
    db = StateDB(data / "state.sqlite")
    logging.info("cam-recorder: site %s, %d cameras, upload %s",
                 cfg.site, len(cfg.cameras), "on" if cfg.upload_enabled else "off")
    asyncio.run(App(cfg, db, data).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
