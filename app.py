"""Entry point for the Mail Analyzer 3.0 app."""

import logging

import config
from mailfilter import create_app

app = create_app()


class _MutePollingAccessLog(logging.Filter):
    """Drop the browser's every-30s ``GET /api/mail`` poll from the request log.

    The UI re-polls /api/mail on a timer to pick up new mail; logging each hit
    floods the console. Every other request (refresh, attachment downloads,
    settings, errors) is left in the log.
    """

    def filter(self, record):
        return "GET /api/mail" not in record.getMessage()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("werkzeug").addFilter(_MutePollingAccessLog())
    # Materialize any Calendar pins whose day is today: move the pinned file from
    # limbo into today's workspace folder (and recreate its manifest record). Pure
    # filesystem, idempotent, runs once at startup before serving requests.
    app.extensions["calendar_materializer"]()
    # Attempt to bring Outlook Desktop online in the background to pull the
    # latest mail; on failure this logs the error and falls back to the
    # cached mail. The periodic scheduler then keeps it up to date.
    app.extensions["mail_initializer"]()
    app.extensions["mail_scheduler"].start()
    # Periodic automation runner (enabled automations whose interval has elapsed).
    app.extensions["automation_scheduler"].start()
    # threaded=True: the dev server otherwise serves ONE request at a time, so a slow
    # Outlook COM call in a Press mailbox probe would stall the mail poll, the search
    # templates, the workspace and every other request behind it. Every store already
    # guards its state with an RLock (see docs/system-design/14), so concurrent
    # requests are safe.
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
