"""Shared test data builders."""


def make_mail(**overrides):
    """A canonical cached-mail dict (pre-derived fields), with overrides applied."""
    mail = {
        "id": "ID1",
        "subject": "Server error report",
        "sender": "Alice Smith",
        "sender_email": "alice@example.com",
        "recipient_names": ["Bob Jones"],
        "recipient_emails": ["bob@example.com"],
        "body": (
            "There was a server error. See http://example.com/log "
            "and https://example.com/log again."
        ),
        "received": "2026-06-10 09:30:00",
        "conversation_id": "CONV1",
        "attachments": [{"filename": "report.pdf"}],
    }
    mail.update(overrides)
    return mail
