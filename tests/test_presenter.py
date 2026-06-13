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


def _view(main=None, optional=None, **overrides):
    mail = MailStore._with_derived(make_mail(**overrides))
    mail["is_thread"] = mail.get("is_thread", False)
    return to_view_model(mail, _node(main), _node(optional))


class ToViewModelTests(unittest.TestCase):
    def test_escapes_subject_sender_and_preview(self):
        vm = _view(subject="<script>x</script>", sender="<b>A</b>", body="<img src=q>")
        self.assertEqual(vm["subject"], "&lt;script&gt;x&lt;/script&gt;")
        self.assertEqual(vm["sender"], "&lt;b&gt;A&lt;/b&gt;")
        self.assertIn("&lt;img", vm["preview"])

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
