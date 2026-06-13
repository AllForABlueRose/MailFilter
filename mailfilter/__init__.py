"""Application factory wiring the components together.

Dependency direction:
    routes -> filters/presenter -> store
    scheduler -> outlook -> store
    store -> crypto            (cache protection at rest)
Only outlook.py and crypto.py import pywin32 (both lazily); only store.py owns
mutable state.
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

    # Exposed for the entry point and for tests.
    #   mail_initializer() — background Outlook bring-up + initial fetch.
    #   mail_scheduler     — periodic refresh thereafter.
    # Both are left for the entry point to start so importing the app
    # (e.g. in tests) never spawns threads or touches Outlook.
    app.extensions["mail_store"] = store
    app.extensions["mail_initializer"] = lambda: outlook.start_async(store)
    app.extensions["mail_scheduler"] = RefreshScheduler(
        config.REFRESH_INTERVAL_SECONDS,
        lambda: outlook.refresh(store),
    )
    return app
