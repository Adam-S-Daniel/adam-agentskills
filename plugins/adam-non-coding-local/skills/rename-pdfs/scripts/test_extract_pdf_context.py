"""Unit tests for find_dates() in extract_pdf_context.

Run:
    python3 -m pytest scripts/test_extract_pdf_context.py -v
or:
    python3 scripts/test_extract_pdf_context.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from extract_pdf_context import find_dates  # noqa: E402


class TestFindDates(unittest.TestCase):
    def test_iso_dash(self):
        self.assertEqual(find_dates("Statement Date: 2024-03-15"), ["2024-03-15"])

    def test_iso_slash(self):
        self.assertEqual(find_dates("dated 2024/03/15 issued"), ["2024-03-15"])

    def test_compact(self):
        self.assertEqual(find_dates("Scan_20240315_001.pdf"), ["2024-03-15"])

    def test_us_slash(self):
        self.assertEqual(find_dates("Invoice Date 03/15/2024"), ["2024-03-15"])

    def test_us_dash_single_digit(self):
        self.assertEqual(find_dates("Issued 3-5-2024"), ["2024-03-05"])

    def test_long_full_month(self):
        self.assertEqual(find_dates("on March 15, 2024 we"), ["2024-03-15"])

    def test_long_abbrev_no_comma(self):
        self.assertEqual(find_dates("Mar 15 2024"), ["2024-03-15"])

    def test_long_with_period(self):
        self.assertEqual(find_dates("Sept. 1, 2024"), ["2024-09-01"])

    def test_day_first_abbrev(self):
        self.assertEqual(find_dates("Statement date 1 Jan 2026"), ["2026-01-01"])

    def test_day_first_full_month_leading_zero(self):
        self.assertEqual(find_dates("Issued 01 January 2026"), ["2026-01-01"])

    def test_day_first_billing_period_range(self):
        self.assertEqual(
            find_dates("Billing Period: 1 Jan 2026 to 31 Jan 2026"),
            ["2026-01-01", "2026-01-31"],
        )

    def test_day_first_ordinal_comma_and_period(self):
        self.assertEqual(find_dates("due 3rd Sept. 2024"), ["2024-09-03"])
        self.assertEqual(find_dates("due 15 March, 2024"), ["2024-03-15"])

    def test_day_first_rejects_invalid_day(self):
        self.assertEqual(find_dates("bogus 31 Feb 2026"), [])

    def test_day_first_needs_a_leading_word_boundary(self):
        # "121 Jan 2026" must not read its trailing "21 Jan 2026" as a date.
        self.assertEqual(find_dates("ref 121 Jan 2026"), [])

    def test_month_first_not_double_counted_as_day_first(self):
        # "Mar 15 2024" is month-first; the day-first pattern must not also
        # read "15 2024" or a neighboring token into a second date.
        self.assertEqual(find_dates("Mar 15 2024"), ["2024-03-15"])
        self.assertEqual(find_dates("Mar 15, 2024 Apr 2 2024"), ["2024-03-15", "2024-04-02"])

    def test_numeric_day_first_stays_unparsed(self):
        # 15/03/2024 is unambiguous but 03/04/2024 is not; numeric day/month
        # forms stay US-only, so the day-first form is month-name only.
        self.assertEqual(find_dates("03/04/2024"), ["2024-03-04"])
        self.assertEqual(find_dates("15/03/2024"), [])

    def test_dedupe_and_order_preserved(self):
        # Two different dates, oldest first; should appear in encounter order, deduped.
        result = find_dates("date 2024-03-15 then later 03/15/2024 and 2024-04-01")
        self.assertEqual(result, ["2024-03-15", "2024-04-01"])

    def test_no_match(self):
        self.assertEqual(find_dates("no dates here at all"), [])

    def test_rejects_invalid_day(self):
        # 2024-02-30 isn't a real date and should be dropped.
        self.assertEqual(find_dates("bogus 2024-02-30"), [])

    def test_ignores_too_old_year(self):
        # Patterns only match 20xx, so 1999 should be ignored.
        self.assertEqual(find_dates("legacy date 1999-12-31"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
