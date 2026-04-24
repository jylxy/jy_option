import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from product_taxonomy import (  # noqa: E402
    get_product_bucket,
    get_product_corr_group,
    normalize_product_key,
)


class ProductTaxonomyTest(unittest.TestCase):
    def test_normalize_product_key(self):
        self.assertEqual(normalize_product_key(" cu "), "CU")
        self.assertEqual(normalize_product_key(510300), "510300")

    def test_known_product_bucket(self):
        self.assertEqual(get_product_bucket("cu"), "base_metals")
        self.assertEqual(get_product_bucket("510300"), "equity_core")

    def test_known_product_corr_group(self):
        self.assertEqual(get_product_corr_group("cu"), "metals_base")
        self.assertEqual(get_product_corr_group("510300"), "equity_cn_large")

    def test_unknown_product_falls_back_to_idiosyncratic_groups(self):
        self.assertEqual(get_product_bucket("xyz"), "other:XYZ")
        self.assertEqual(get_product_corr_group("xyz"), "idio:XYZ")


if __name__ == "__main__":
    unittest.main()
