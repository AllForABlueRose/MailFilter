"""Application factory wiring the components together.

Dependency direction:
    routes -> filters/presenter -> store
    routes -> settings_store, tag_store
    scheduler -> outlook -> store
    bootstrap -> outlook -> store
    store, settings_store, tag_store -> crypto    (cache protection at rest)
Only outlook.py and crypto.py import pywin32 (both lazily); store.py,
settings_store.py and tag_store.py own mutable state.
"""

from flask import Flask

import config

from . import bootstrap, outlook
from .routes import create_blueprint
from .scheduler import RefreshScheduler
from .settings_store import SettingsStore
from .store import MailStore
from .tag_store import TagStore


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

    tags = TagStore(config.TAGS_FILE)
    tags.load()

    app.register_blueprint(create_blueprint(store, settings, tags))

    # Exposed for the entry point and for tests.
    #   mail_initializer() — background Outlook bring-up + initial fetch. On a
    #     cold start (no complete cache) this is the optimized full sync owned by
    #     bootstrap.py; otherwise it is the normal incremental bring-up.
    #   mail_scheduler     — periodic refresh thereafter.
    # Both are left for the entry point to start so importing the app
    # (e.g. in tests) never spawns threads or touches Outlook.
    app.extensions["mail_store"] = store
    app.extensions["settings_store"] = settings
    app.extensions["tag_store"] = tags
    app.extensions["mail_initializer"] = lambda: _start_initializer(store)
    app.extensions["mail_scheduler"] = RefreshScheduler(
        config.REFRESH_INTERVAL_SECONDS,
        lambda: outlook.refresh(store),
    )
    return app


def _start_initializer(store):
    """Bring Outlook online at startup, choosing the cold-start full sync when
    no complete cache exists yet and the normal incremental bring-up otherwise."""
    if bootstrap.needs_bootstrap():
        return bootstrap.start_async(store)
    return outlook.start_async(store)
