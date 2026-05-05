# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from magazarr.automation import AutomationService
from magazarr.db import Database
from magazarr.server import Server
from magazarr.settings import SettingsStore
from magazarr.web import create_app

load_dotenv(override=True)


def _data_dir() -> Path:
    if os.environ.get("DOCKER"):
        return Path("/config")
    return Path(os.environ.get("MAGAZARR_CONFIG_DIR", "data")).expanduser()


def run():
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stdout, level=os.environ.get("LOG_LEVEL", "INFO"))

    settings = SettingsStore(data_dir / "settings.json")
    db = Database(data_dir / "magazarr.db")
    db.migrate()

    port = int(os.environ.get("PORT", "8090"))
    listen = os.environ.get("LISTEN", "127.0.0.1")
    automation = AutomationService(settings, db)
    app = create_app(settings, db, automation)
    automation.start()

    logger.info(f"Magazarr listening on http://{listen}:{port}")
    Server(app, listen=listen, port=port).serve_forever()
