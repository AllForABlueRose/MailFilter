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
    # Attempt to bring Outlook Desktop online in the background to pull the
    # latest mail; on failure this logs the error and falls back to the
    # cached mail. The periodic scheduler then keeps it up to date.
    app.extensions["mail_initializer"]()
    app.extensions["mail_scheduler"].start()
    # Periodic automation runner (enabled automations whose interval has elapsed).
    app.extensions["automation_scheduler"].start()
    app.run(host=config.HOST, port=config.PORT, debug=False)
