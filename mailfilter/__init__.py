"""Application factory wiring the components together.

Dependency direction:
    routes -> filters/presenter -> store
    routes -> settings_store, tag_store, template_store
    routes -> customers, customer_store
    template_store -> settings_store (schema), imgcodec (PNG files), util
    customers -> (nothing app-level: pure, orgs passed in)
    customer_store -> persistence -> crypto
    scheduler -> outlook -> store
    bootstrap -> outlook -> store
    store, settings_store, tag_store, customer_store -> crypto  (protection at rest)
Only outlook.py and crypto.py import pywin32 (both lazily); store.py,
settings_store.py, tag_store.py, template_store.py and customer_store.py own
mutable state.
"""

from flask import Flask

import config

from . import automation, bootstrap, outlook
from .automation_store import AutomationStore
from .compose_template_store import ComposeTemplateStore
from .customer_match_store import CustomerMatchStore
from .customer_store import CustomerStore
from .experimental_store import ExperimentalStore
from .password_settings_store import PasswordSettingsStore
from .routes import create_blueprint, refresh_then_scan
from .scheduler import RefreshScheduler
from .settings_store import SettingsStore
from .store import MailStore
from .tag_store import TagStore
from .template_store import TemplateStore
from .vault_store import VaultStore


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

    templates = TemplateStore(config.TEMPLATES_DIR)
    templates.load()

    automations = AutomationStore(config.AUTOMATIONS_FILE)
    automations.load()

    customers = CustomerStore(config.CUSTOMERS_FILE)
    customers.load()

    compose_templates = ComposeTemplateStore(config.COMPOSE_TEMPLATES_FILE)
    compose_templates.load()

    password_settings = PasswordSettingsStore(config.PASSWORD_SETTINGS_FILE)
    password_settings.load()

    experimental = ExperimentalStore(config.EXPERIMENTAL_FILE)
    experimental.load()

    customer_match = CustomerMatchStore(config.CUSTOMER_MATCH_FILE)
    customer_match.load()

    # The Key Vault reads its files lazily (and only decrypts once unlocked), so
    # there is nothing to load at startup — it begins locked.
    vault = VaultStore(config.VAULT_FILE, config.VAULT_INDEX_FILE, config.VAULT_KEY_DPAPI_FILE)

    app.register_blueprint(
        create_blueprint(store, settings, tags, templates, automations, customers,
                         compose_templates, password_settings, experimental,
                         customer_match, vault)
    )

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
    app.extensions["template_store"] = templates
    app.extensions["automation_store"] = automations
    app.extensions["customer_store"] = customers
    app.extensions["compose_template_store"] = compose_templates
    app.extensions["password_settings_store"] = password_settings
    app.extensions["experimental_store"] = experimental
    app.extensions["customer_match_store"] = customer_match
    app.extensions["vault_store"] = vault
    app.extensions["mail_initializer"] = lambda: _start_initializer(store)
    # Each periodic refresh fetches + syncs mail, then runs the SDS scan (read-only;
    # captures into the vault only while it is unlocked).
    app.extensions["mail_scheduler"] = RefreshScheduler(
        config.REFRESH_INTERVAL_SECONDS,
        lambda: refresh_then_scan(store, password_settings, vault, customers),
    )
    # Ticks every AUTOMATION_TICK_SECONDS and runs each enabled automation whose
    # interval has elapsed. Like mail_scheduler, the entry point owns start().
    app.extensions["automation_scheduler"] = RefreshScheduler(
        config.AUTOMATION_TICK_SECONDS,
        lambda: automation.run_due_automations(automations, store, tags),
    )
    return app


def _start_initializer(store):
    """Bring Outlook online at startup, choosing the cold-start full sync when
    no complete cache exists yet and the normal incremental bring-up otherwise."""
    if bootstrap.needs_bootstrap():
        return bootstrap.start_async(store)
    return outlook.start_async(store)
