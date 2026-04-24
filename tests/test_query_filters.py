import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from query_filters import (  # noqa: E402
    build_code_filter_sql,
    build_product_like_sql,
    iter_code_filter_sql,
    normalize_product_pool,
    quote_sql_literal,
)


class QueryFilterTest(unittest.TestCase):
    def test_quote_sql_literal_escapes_single_quotes(self):
        self.assertEqual(quote_sql_literal("A'B"), "'A''B'")

    def test_build_code_filter_sql_sorts_deduplicates_and_chunks(self):
        sql = build_code_filter_sql(["B", "A", "A", None, " "], chunk_size=1)
        self.assertEqual(sql, "ths_code IN ('A') OR ths_code IN ('B')")

    def test_iter_code_filter_sql_supports_custom_column(self):
        parts = list(iter_code_filter_sql(["X", "Y"], column_name="code", chunk_size=10))
        self.assertEqual(parts, ["code IN ('X', 'Y')"])

    def test_normalize_product_pool(self):
        self.assertEqual(normalize_product_pool([" cu ", "CU", 510300, None]), ("510300", "CU"))

    def test_build_product_like_sql_mixes_futures_and_etf_filters(self):
        contract_cache = {
            "CU2505.SHF": {"product_root": "CU"},
            "CU2506.SHF": {"product_root": "CU"},
            "510300C2505M04000.SH": {"product_root": "510300"},
        }

        def product_code_lookup(product):
            if product == "510300":
                return ["510300C2505M04000.SH", "510300P2505M04000.SH"]
            return []

        sql = build_product_like_sql(["cu", "510300"], contract_cache, product_code_lookup)
        self.assertEqual(
            sql,
            "ths_code LIKE 'CU%.SHF' OR ths_code IN ('510300C2505M04000.SH', '510300P2505M04000.SH')",
        )


if __name__ == "__main__":
    unittest.main()
