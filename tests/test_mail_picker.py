"""Unit tests for the shared cache-mail picker (mailfilter/mail_picker.py).

Composer picks one mail to preview against; Press loads many into its worklist. Both
go through these filters and this pager, so it is tested once here.
"""

import unittest

from mailfilter import mail_picker


def no_org(mail):
    return None


def no_tags(mail_id):
    return {}


class FilterTests(unittest.TestCase):
    def test_filter_ids_are_unique(self):
        self.assertEqual(len(set(mail_picker.FILTER_IDS)), len(mail_picker.FILTER_IDS))
        self.assertIn("all", mail_picker.FILTER_IDS)

    def test_all_matches_everything(self):
        self.assertTrue(mail_picker.matches({"id": "M1"}, "all", no_org, no_tags))

    def test_unknown_filter_falls_back_to_showing_everything(self):
        self.assertTrue(mail_picker.matches({"id": "M1"}, "bogus", no_org, no_tags))

    def test_derived_field_filters(self):
        mail = {"id": "M1", "_has_attachments": True, "_has_links": False,
                "_has_password": True}
        self.assertTrue(mail_picker.matches(mail, "attachments", no_org, no_tags))
        self.assertFalse(mail_picker.matches(mail, "links", no_org, no_tags))
        self.assertTrue(mail_picker.matches(mail, "password", no_org, no_tags))

    def test_org_filter_uses_the_supplied_resolver(self):
        mail = {"id": "M1"}
        self.assertFalse(mail_picker.matches(mail, "org", no_org, no_tags))
        self.assertTrue(mail_picker.matches(
            mail, "org", lambda m: {"id": "O1", "name": "Acme"}, no_tags))

    def test_tag_filter_uses_the_supplied_tag_lookup(self):
        mail = {"id": "M1"}
        self.assertFalse(mail_picker.matches(mail, "tag", no_org, no_tags))
        self.assertTrue(mail_picker.matches(
            mail, "tag", no_org, lambda mid: {"downloaded": "recent"}))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.mails = [{"id": f"M{i}", "_has_attachments": i % 2 == 0} for i in range(25)]

    def _page(self, offset, limit, filter_id="all"):
        return mail_picker.page(self.mails, filter_id, offset, limit, no_org, no_tags)

    def test_pages_do_not_overlap_and_cover_everything(self):
        first = self._page(0, 10)
        second = self._page(10, 10)
        third = self._page(20, 10)
        self.assertEqual([m["id"] for m in first["mails"]], [f"M{i}" for i in range(10)])
        self.assertEqual([m["id"] for m in second["mails"]],
                         [f"M{i}" for i in range(10, 20)])
        self.assertEqual(len(third["mails"]), 5)
        self.assertTrue(first["has_more"])
        self.assertFalse(third["has_more"])

    def test_total_counts_the_filtered_set_not_the_page(self):
        result = self._page(0, 10, "attachments")
        self.assertEqual(result["total"], 13)
        self.assertEqual(len(result["mails"]), 10)
        self.assertTrue(result["has_more"])

    def test_offset_past_the_end_is_an_empty_last_page(self):
        result = self._page(99, 10)
        self.assertEqual(result["mails"], [])
        self.assertFalse(result["has_more"])


class CardTests(unittest.TestCase):
    def test_card_is_raw_strings_for_dom_text_insertion(self):
        mail = {"id": "M1", "subject": "<b>hi</b>", "sender": "Alice",
                "sender_email": "a@x.com", "received": "2026-06-10 09:30:00",
                "_has_attachments": True}
        card = mail_picker.card(mail)
        # Not escaped: this is a picker, and the frontend inserts it as DOM text.
        self.assertEqual(card["subject"], "<b>hi</b>")
        self.assertEqual(card["sender"], {"name": "Alice", "email": "a@x.com"})
        self.assertTrue(card["has_attachments"])
        self.assertEqual(card["org_labels"], [])

    def test_org_label_and_tags_are_attached_when_supplied(self):
        card = mail_picker.card({"id": "M1"}, org_label={"name": "Acme"},
                                tags={"downloaded": "recent"})
        self.assertEqual(card["org_labels"], [{"name": "Acme"}])
        self.assertEqual(card["tags"], {"downloaded": "recent"})


if __name__ == "__main__":
    unittest.main()
