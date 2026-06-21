"""Tests for mailfilter.spreadsheet: .xlsx -> normalized Bulk Compose rows.

Workbooks are built in-memory with openpyxl (already a project dependency), so no
fixture files are needed.
"""

import unittest
from datetime import datetime
from io import BytesIO

import openpyxl

from mailfilter import spreadsheet
from mailfilter.spreadsheet import SpreadsheetError, parse_xlsx


def _xlsx(rows):
    """Serialize ``rows`` (list of lists, first row = headers) to .xlsx bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ParseTests(unittest.TestCase):
    def test_basic_headers_and_rows(self):
        data = _xlsx([
            ["Subject", "Sender", "File Name"],
            ["Invoice", "alice@example.com", "inv.pdf"],
        ])
        headers, rows, dropped = parse_xlsx(data)
        self.assertEqual(headers, ["subject", "sender", "file name"])
        self.assertEqual(dropped, 0)
        self.assertEqual(rows[0]["subject"], "Invoice")
        self.assertEqual(rows[0]["sender"], "alice@example.com")

    def test_canonical_aliases(self):
        data = _xlsx([
            ["Subject", "File Name", "FTP"],
            ["Hi", "report.pdf", "Yes"],
        ])
        _h, rows, _d = parse_xlsx(data)
        # "file name" -> file_name, "ftp" -> uses_ftp aliases are added.
        self.assertEqual(rows[0]["file_name"], "report.pdf")
        self.assertEqual(rows[0]["uses_ftp"], "Yes")
        # Raw header keys still present too.
        self.assertEqual(rows[0]["file name"], "report.pdf")

    def test_datetime_cell_uses_received_format(self):
        data = _xlsx([
            ["Subject", "Datetime"],
            ["Hi", datetime(2026, 6, 20, 14, 30, 0)],
        ])
        _h, rows, _d = parse_xlsx(data)
        self.assertEqual(rows[0]["datetime"], "2026-06-20 14:30:00")

    def test_integer_float_cell_has_no_trailing_zero(self):
        data = _xlsx([["Subject", "Ref"], ["Hi", 12.0]])
        _h, rows, _d = parse_xlsx(data)
        self.assertEqual(rows[0]["ref"], "12")

    def test_blank_rows_skipped(self):
        data = _xlsx([
            ["Subject"],
            ["A"],
            [None],
            ["", ],
            ["B"],
        ])
        _h, rows, _d = parse_xlsx(data)
        self.assertEqual([r["subject"] for r in rows], ["A", "B"])

    def test_max_rows_cap_reports_dropped(self):
        data = _xlsx([["Subject"]] + [[f"row{i}"] for i in range(5)])
        _h, rows, dropped = parse_xlsx(data, max_rows=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(dropped, 2)

    def test_missing_cells_become_empty_string(self):
        data = _xlsx([["Subject", "Sender", "File Name"], ["OnlySubject"]])
        _h, rows, _d = parse_xlsx(data)
        self.assertEqual(rows[0]["sender"], "")
        self.assertEqual(rows[0]["file_name"], "")


class ErrorTests(unittest.TestCase):
    def test_non_xlsx_bytes_raise(self):
        with self.assertRaises(SpreadsheetError):
            parse_xlsx(b"this is not a workbook")

    def test_empty_sheet_raises(self):
        with self.assertRaises(SpreadsheetError):
            parse_xlsx(_xlsx([]))

    def test_headerless_sheet_raises(self):
        # A first row of all-empty cells is not a usable header.
        with self.assertRaises(SpreadsheetError):
            parse_xlsx(_xlsx([[None, None], ["a", "b"]]))


if __name__ == "__main__":
    unittest.main()
