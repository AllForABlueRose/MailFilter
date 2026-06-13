"""Tests for mailfilter.presenter: view-model shaping, escaping, highlighting."""

import unittest

from mailfilter.presenter import _highlight, to_view_model
from mailfilter.store import MailStore
from tests.factories import make_mail


def _view(highlight=None, **overrides):
    mail = MailStore._with_derived(make_mail(**overrides))
    mail["is_thread"] = mail.get("is_thread", False)
    return to_view_model(mail, highlight or [])


class ToViewModelTests(unittest.TestCase):
    def test_escapes_subject_sender_and_preview(self):
        vm = _view(subject="<script>x</script>", sender="<b>A</b>",
                   body="<img src=q>")
        self.assertEqual(vm["subject"], "&lt;script&gt;x&lt;/script&gt;")
        self.assertEqual(vm["sender"], "&lt;b&gt;A&lt;/b&gt;")
        self.assertIn("&lt;img", vm["preview"])

    def test_newlines_become_br(self):
        vm = _view(body="line1\nline2")
        self.assertIn("line1<br>line2", vm["preview"])

    def test_icon_reflects_thread_flag(self):
        self.assertEqual(_view(is_thread=True)["icon"], "🧵")
        self.assertEqual(_view(is_thread=False)["icon"], "✉️")

    def test_attachment_urls_keyed_by_id_and_index(self):
        vm = _view(id="MAIL 1", attachments=[{"filename": "a.pdf"}, {"filename": "b.pdf"}])
        urls = [a["url"] for a in vm["attachments"]]
        # space in id must be percent-encoded into the path
        self.assertEqual(urls, ["/attachments/MAIL%201/0", "/attachments/MAIL%201/1"])
        self.assertEqual([a["filename"] for a in vm["attachments"]], ["a.pdf", "b.pdf"])

    def test_links_come_from_derived_field(self):
        vm = _view()
        self.assertEqual(vm["links"], ["http://example.com/log", "https://example.com/log"])


class HighlightTests(unittest.TestCase):
    def test_wraps_matches_case_insensitively(self):
        out = _highlight("an Urgent and urgent note", ["urgent"])
        self.assertEqual(out.count('<span class="highlight">'), 2)
        self.assertIn('<span class="highlight">Urgent</span>', out)

    def test_no_keywords_returns_text_unchanged(self):
        self.assertEqual(_highlight("hello", []), "hello")

    def test_keyword_escaped_like_text(self):
        # "R&D" in source text is escaped to "R&amp;D"; the keyword must be
        # escaped the same way to still match.
        escaped = "investing in R&amp;D heavily"
        out = _highlight(escaped, ["R&D"])
        self.assertIn('<span class="highlight">R&amp;D</span>', out)


if __name__ == "__main__":
    unittest.main()
