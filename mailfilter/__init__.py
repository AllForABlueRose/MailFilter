"""Application factory wiring the components together.

Dependency direction:
    routes -> filters/presenter -> store
    scheduler -> outlook -> store
Only outlook.py knows pywin32 exists; only store.py owns mutable state.
"""

from flask import Flask

import config

from . import outlook
from .routes import create_blueprint
from .scheduler import RefreshScheduler
from .store import MailStore


def create_app():
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )

    store = MailStore(config.CACHE_FILE)
    store.load()

    app.register_blueprint(create_blueprint(store))

    # Exposed for the entry point (scheduler.start()) and for tests.
    app.extensions["mail_store"] = store
    app.extensions["mail_scheduler"] = RefreshScheduler(
        config.REFRESH_INTERVAL_SECONDS,
        lambda: outlook.refresh(store),
    )
    return app
