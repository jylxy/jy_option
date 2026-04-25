import os
import sys
import tempfile
import unittest

import pandas as pd


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contract_provider import ContractInfo


def fake_selector(sql):
    if "option_basic_info" in sql:
        return pd.DataFrame([
            {
                "ths_code": "CU2506C76000.SHF",
                "option_short_name": "沪铜2506购76000",
                "contract_type": "call",
                "strike_price": 76000,
                "maturity_date": "2025-06-20",
                "last_strike_date": "2025-06-20",
                "contract_multiplier": 5,
                "strike_method": "美式",
            },
            {
                "ths_code": "IO2506-P-3600.CFE",
                "option_short_name": "沪深300股指期权",
                "contract_type": "put",
                "strike_price": 3600,
                "maturity_date": "2025-06-20",
                "last_strike_date": "2025-06-20",
                "contract_multiplier": 100,
                "strike_method": "欧式",
            },
            {
                "ths_code": "10007959.SH",
                "option_short_name": "50ETF购2.80",
                "contract_type": "call",
                "strike_price": 2.8,
                "maturity_date": "2025-06-25",
                "last_strike_date": "2025-06-25",
                "contract_multiplier": 10000,
                "strike_method": "欧式",
            },
        ])
    if "future_basic_info" in sql:
        return pd.DataFrame([
            {"ths_code": "CU2506.SHF", "initial_td_deposit": "8%"},
            {"ths_code": "IF2506.CFE", "initial_td_deposit": "12%"},
        ])
    raise AssertionError(f"unexpected query: {sql}")


class ContractProviderTest(unittest.TestCase):
    def test_load_contract_metadata_and_margin_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            ci = ContractInfo(cache_dir=tmp, selector=fake_selector)
            ci.load()

            cu = ci.lookup("CU2506C76000.SHF")
            self.assertEqual(cu["option_type"], "C")
            self.assertEqual(cu["exchange"], "SHFE")
            self.assertEqual(cu["product_root"], "CU")
            self.assertEqual(cu["underlying_code"], "CU2506.SHF")
            self.assertEqual(cu["multiplier"], 5)
            self.assertEqual(ci.calc_dte("CU2506C76000.SHF", "2025-06-01"), 19)

            io = ci.lookup("IO2506-P-3600.CFE")
            self.assertEqual(io["exchange"], "CFFEX")
            self.assertEqual(io["product_root"], "IO")
            self.assertEqual(io["underlying_code"], "IF2506.CFE")

            etf = ci.lookup("10007959.SH")
            self.assertEqual(etf["exchange"], "SSE")
            self.assertEqual(etf["product_root"], "510050")
            self.assertEqual(etf["underlying_code"], "510050.SH")

            # Broker product-level margin ratios are the primary production path.
            self.assertAlmostEqual(
                ci.get_margin_ratio("SHFE", "CU", "CU2506.SHF", {}),
                0.10,
            )

            # Index option roots map to their index futures margin ratios.
            self.assertAlmostEqual(
                ci.get_margin_ratio("CFFEX", "IO", "IF2506.CFE", {}),
                0.12,
            )


if __name__ == "__main__":
    unittest.main()
