"""Tests for mailfilter.presenter: view-model shaping, escaping, highlighting."""

import unittest

from mailfilter import expr
from mailfilter.presenter import _highlight, to_view_model
from mailfilter.store import MailStore
from tests.factories import make_mail


def _node(expression):
    return expr.parse(expression) if expression else None


def _terms(expression):
    return expr.operands(_node(expression))


def _view(main=None, optional=None, attachment_blacklist=None, links_blacklist=None, **overrides):
    mail = MailStore._with_derived(make_mail(**overrides))
    mail["is_thread"] = mail.get("is_thread", False)
    return to_view_model(mail, _node(main), _node(optional),
                         _node(attachment_blacklist), _node(links_blacklist))


class ToViewModelTests(unittest.TestCase):
    def test_escapes_subject_and_preview(self):
        vm = _view(subject="<script>x</script>", body="<img src=q>")
        self.assertEqual(vm["subject"], "&lt;script&gt;x&lt;/script&gt;")
        self.assertIn("&lt;img", vm["preview"])

    def test_people_are_structured_and_raw(self):
        # People are inserted via the DOM as text, so they stay raw (unescaped).
        vm = _view(sender="<b>A</b>", sender_email="a@x.com",
                   recipient_names=["Bob"], recipient_emails=["bob@x.com"],
                   cc_names=["Carol"], cc_emails=["carol@x.com"])
        self.assertEqual(vm["sender"], {"name": "<b>A</b>", "email": "a@x.com"})
        self.assertEqual(vm["recipients"], [{"name": "Bob", "email": "bob@x.com"}])
        self.assertEqual(vm["cc"], [{"name": "Carol", "email": "carol@x.com"}])

    def test_person_with_missing_name_keeps_email(self):
        vm = _view(recipient_names=[""], recipient_emails=["noname@x.com"])
        self.assertEqual(vm["recipients"], [{"name": "", "email": "noname@x.com"}])

    def test_cc_empty_when_absent(self):
        self.assertEqual(_view()["cc"], [])

    def test_newlines_become_br(self):
        self.assertIn("line1<br>line2", _view(body="line1\nline2")["preview"])

    def test_icon_reflects_thread_flag(self):
        self.assertEqual(_view(is_thread=True)["icon"], "🧵")
        self.assertEqual(_view(is_thread=False)["icon"], "✉️")

    def test_view_model_includes_id(self):
        self.assertEqual(_view(id="ABC123")["id"], "ABC123")

    def test_attachment_urls_keyed_by_id_and_index(self):
        vm = _view(id="MAIL 1", attachments=[{"filename": "a.pdf"}, {"filename": "b.pdf"}])
        self.assertEqual(
            [a["url"] for a in vm["attachments"]],
            ["/attachments/MAIL%201/0", "/attachments/MAIL%201/1"],
        )

    def test_main_and_optional_highlighted_in_distinct_classes(self):
        vm = _view(main="alpha", optional="beta", body="alpha and beta")
        self.assertIn('<span class="highlight-main">alpha</span>', vm["preview"])
        self.assertIn('<span class="highlight">beta</span>', vm["preview"])

    def test_attachment_filename_highlighted_raw_kept(self):
        att = _view(main="report", attachments=[{"filename": "report.pdf"}])["attachments"][0]
        self.assertEqual(att["filename"], "report.pdf")   # raw value preserved
        self.assertIn('<span class="highlight-main">report</span>', att["filename_html"])

    def test_link_url_highlighted_raw_kept(self):
        link = _view(optional="example", body="see https://example.com/x")["links"][0]
        self.assertEqual(link["url"], "https://example.com/x")   # raw value preserved
        self.assertIn('<span class="highlight">example</span>', link["url_html"])

    def test_attachment_blacklist_omits_and_keeps_original_index(self):
        vm = _view(attachment_blacklist=".exe",
                   attachments=[{"filename": "setup.exe"}, {"filename": "report.pdf"}])
        self.assertEqual([a["filename"] for a in vm["attachments"]], ["report.pdf"])
        # the surviving attachment keeps its ORIGINAL position (1)
        self.assertEqual(vm["attachments"][0]["index"], 1)
        self.assertTrue(vm["attachments"][0]["url"].endswith("/1"))

    def test_links_blacklist_omits_matching(self):
        vm = _view(links_blacklist="track", body="a https://track.me/x and https://good.com/y")
        self.assertEqual([l["url"] for l in vm["links"]], ["https://good.com/y"])


class HighlightTests(unittest.TestCase):
    def test_main_uses_distinct_class_from_optional(self):
        out = _highlight("alpha beta", _terms("alpha"), _terms("beta"))
        self.assertIn('<span class="highlight-main">alpha</span>', out)
        self.assertIn('<span class="highlight">beta</span>', out)

    def test_no_terms_returns_text_unchanged(self):
        self.assertEqual(_highlight("hello", [], []), "hello")

    def test_main_wins_over_optional_on_overlap(self):
        out = _highlight("urgent", _terms("urgent"), _terms("urgent"))
        self.assertIn('<span class="highlight-main">urgent</span>', out)
        self.assertNotIn('<span class="highlight">urgent</span>', out)

    def test_regex_term_is_highlighted(self):
        out = _highlight("the grey cat", _terms("<{(gr(a|e)y)}>"), [])
        self.assertIn('<span class="highlight-main">grey</span>', out)

    def test_keyword_escaped_like_text(self):
        # "R&D" in source is escaped to "R&amp;D"; the literal must match it.
        out = _highlight("investing in R&amp;D", [], _terms("R&D"))
        self.assertIn('<span class="highlight">R&amp;D</span>', out)


if __name__ == "__main__":
    unittest.main()
