"""Entry point for the Mail Analyzer app."""

import logging

import config
from mailfilter import create_app

app = create_app()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Attempt to bring Outlook Desktop online in the background to pull the
    # latest mail; on failure this logs the error and falls back to the
    # cached mail. The periodic scheduler then keeps it up to date.
    app.extensions["mail_initializer"]()
    app.extensions["mail_scheduler"].start()
    app.run(host=config.HOST, port=config.PORT, debug=False)
