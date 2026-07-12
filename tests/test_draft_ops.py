"""Tests for draft_ops: the app's only mailbox write, and it only ever writes DRAFTS.

There is no mock mode any more, so these drive the **real COM code path** against
stubbed Outlook objects. That is the point: the stubs record exactly what the module
does to Outlook, which is how we prove it calls ``Save()`` and never ``Send()``.
"""

import unittest
from unittest import mock

from mailfilter import draft_ops


class FakeRecipient:
    def __init__(self, address):
        self.address = address
        self.Type = None


class FakeRecipients:
    def __init__(self):
        self.added = []
        self.resolved = False

    def Add(self, address):
        r = FakeRecipient(address)
        self.added.append(r)
        return r

    def ResolveAll(self):
        self.resolved = True


class FakeAttachments:
    def __init__(self):
        self.paths = []

    def Add(self, path):
        self.paths.append(path)


class FakeReply:
    def __init__(self):
        self.Body = "\n> original quoted history"
        self.SentOnBehalfOfName = None
        self.Recipients = FakeRecipients()
        self.Attachments = FakeAttachments()
        self.saved = False
        self.sent = False

    def Save(self):
        self.saved = True

    def Send(self):           # must never be called
        self.sent = True


class FakeItem:
    def __init__(self, reply):
        self._reply = reply

    def ReplyAll(self):
        return self._reply


class FakeNamespace:
    def __init__(self, reply):
        self._reply = reply
        self.get_calls = []

    def GetItemFromID(self, entry_id, store_id=None):
        self.get_calls.append((entry_id, store_id))
        return FakeItem(self._reply)


class FakePythoncom:
    @staticmethod
    def CoInitialize():
        pass

    @staticmethod
    def CoUninitialize():
        pass


def plan(**kw):
    base = {
        "mail_id": "ENTRY-1", "row_index": 0, "status": "ready",
        "subject": "RE: Invoice", "body": "Dear Alice,\nHere it is.",
        "to": ["alice@acme.com"], "cc": ["shared@example.com"],
        "uses_ftp": False, "ftp_link": "",
        "attachment": {"name": "inv.pdf", "path": "/files/inv.pdf", "exists": True},
    }
    base.update(kw)
    return base


class DraftOpsTests(unittest.TestCase):
    def _run(self, plans, sender="shared@example.com", cc="shared@example.com"):
        self.reply = FakeReply()
        self.ns = FakeNamespace(self.reply)
        app = mock.Mock()
        app.GetNamespace.return_value = self.ns
        with mock.patch.object(draft_ops.outlook, "_import_pywin32",
                               return_value=(FakePythoncom, None, None)), \
             mock.patch.object(draft_ops.outlook, "_dispatch", return_value=app):
            return draft_ops.create_drafts(plans, sender, cc)

    # ----- the write itself -----

    def test_saves_a_draft_and_never_sends(self):
        results = self._run([plan()])
        self.assertEqual(results[0]["status"], "created")
        self.assertTrue(self.reply.saved)
        self.assertFalse(self.reply.sent)      # THE invariant

    def test_reply_all_is_used_on_the_original_by_entry_id(self):
        self._run([plan()])
        self.assertEqual(self.ns.get_calls, [("ENTRY-1", None)])

    def test_a_cache_mail_with_no_store_id_uses_the_single_arg_lookup(self):
        # Cache mail carries only its Outlook EntryID; that alone re-opens it, which
        # is what lets Press reply to ordinary cached mail.
        self._run([plan(store_id="")])
        self.assertIsNone(self.ns.get_calls[0][1])

    def test_a_store_id_is_honoured_when_present(self):
        self._run([plan(store_id="STORE-9")])
        self.assertEqual(self.ns.get_calls[0], ("ENTRY-1", "STORE-9"))

    def test_sent_on_behalf_of_is_the_selected_mailbox(self):
        self._run([plan()], sender="team@example.com")
        self.assertEqual(self.reply.SentOnBehalfOfName, "team@example.com")

    def test_the_cc_address_is_added_and_resolved(self):
        self._run([plan()], cc="team@example.com")
        self.assertEqual([r.address for r in self.reply.Recipients.added],
                         ["team@example.com"])
        self.assertEqual(self.reply.Recipients.added[0].Type, draft_ops.OL_CC)
        self.assertTrue(self.reply.Recipients.resolved)

    def test_no_cc_is_added_when_the_toggle_is_off(self):
        self._run([plan()], cc="")
        self.assertEqual(self.reply.Recipients.added, [])

    def test_the_body_goes_above_the_quoted_history(self):
        self._run([plan()])
        self.assertTrue(self.reply.Body.startswith("Dear Alice,\nHere it is."))
        self.assertIn("> original quoted history", self.reply.Body)

    # ----- attachments -----

    def test_an_existing_file_is_attached(self):
        self._run([plan()])
        self.assertEqual(self.reply.Attachments.paths, ["/files/inv.pdf"])

    def test_an_ftp_plan_attaches_nothing(self):
        self._run([plan(uses_ftp=True, ftp_link="ftp://x/inv.pdf")])
        self.assertEqual(self.reply.Attachments.paths, [])

    def test_a_missing_file_is_not_attached(self):
        self._run([plan(attachment={"name": "x", "path": "/files/x", "exists": False})])
        self.assertEqual(self.reply.Attachments.paths, [])

    # ----- the gates -----

    def test_non_ready_plans_are_skipped_not_created(self):
        results = self._run([plan(status="blocked")])
        self.assertEqual(results[0]["status"], "skipped")
        self.assertFalse(self.reply.saved)

    def test_no_draft_is_created_without_a_verified_sender(self):
        # Belt and braces: the route refuses this already.
        results = self._run([plan()], sender="")
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("no verified mailbox", results[0]["detail"])
        self.assertFalse(self.reply.saved)

    def test_a_com_failure_on_one_plan_is_reported_not_fatal(self):
        reply = FakeReply()
        ns = FakeNamespace(reply)
        ns.GetItemFromID = mock.Mock(side_effect=RuntimeError("item is gone"))
        app = mock.Mock()
        app.GetNamespace.return_value = ns
        with mock.patch.object(draft_ops.outlook, "_import_pywin32",
                               return_value=(FakePythoncom, None, None)), \
             mock.patch.object(draft_ops.outlook, "_dispatch", return_value=app):
            results = draft_ops.create_drafts([plan()], "me@example.com", "")
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("item is gone", results[0]["detail"])
        self.assertFalse(reply.saved)

    def test_results_carry_the_mail_id_back(self):
        results = self._run([plan(mail_id="ENTRY-7")])
        self.assertEqual(results[0]["mail_id"], "ENTRY-7")

    def test_nothing_ready_means_com_is_never_touched(self):
        with mock.patch.object(draft_ops.outlook, "_import_pywin32") as imp:
            results = draft_ops.create_drafts([plan(status="blocked")], "me@x.com", "")
        imp.assert_not_called()
        self.assertEqual(results[0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
