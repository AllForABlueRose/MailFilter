"""Application factory wiring the components together.

Dependency direction:
    routes -> filters/presenter -> store
    routes -> settings_store
    scheduler -> outlook -> store
    store, settings_store -> crypto    (cache protection at rest)
Only outlook.py and crypto.py import pywin32 (both lazily); store.py and
settings_store.py own mutable state.
"""

from flask import Flask

import config

from . import outlook
from .routes import create_blueprint
from .scheduler import RefreshScheduler
from .settings_store import SettingsStore
from .store import MailStore


def create_app():
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )

    store = MailStore(config.CACHE_FILE)
    store.load()

    settings = SettingsStore(config.SETTINGS_FILE)
    settings.load()

    app.register_blueprint(create_blueprint(store, settings))

    # Exposed for the entry point and for tests.
    #   mail_initializer() — background Outlook bring-up + initial fetch.
    #   mail_scheduler     — periodic refresh thereafter.
    # Both are left for the entry point to start so importing the app
    # (e.g. in tests) never spawns threads or touches Outlook.
    app.extensions["mail_store"] = store
    app.extensions["settings_store"] = settings
    app.extensions["mail_initializer"] = lambda: outlook.start_async(store)
    app.extensions["mail_scheduler"] = RefreshScheduler(
        config.REFRESH_INTERVAL_SECONDS,
        lambda: outlook.refresh(store),
    )
    return app
