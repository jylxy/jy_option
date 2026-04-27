import json
from pathlib import Path


def test_b0_standard_config_is_plain_short_premium_baseline():
    cfg_path = Path(__file__).resolve().parents[1] / "config_s1_baseline_b0_all_products_stop25.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    assert cfg["s1_baseline_mode"] is True
    assert cfg["strategy_version"] == "s1_baseline_b0_standard_stop25"
    assert cfg["margin_cap"] == 0.50
    assert cfg["s1_margin_cap"] == 0.50
    assert cfg["s1_expiry_mode"] == "nth_expiry"
    assert cfg["s1_expiry_rank"] == 2
    assert cfg["s1_sell_delta_cap"] == 0.10
    assert cfg["s1_baseline_max_contracts_per_side"] == 5
    assert cfg["s1_allow_add_same_side"] is True
    assert cfg["s1_allow_add_same_expiry"] is True
    assert cfg["s1_min_premium_fee_multiple"] == 1.0

    assert cfg["s1_protect_enabled"] is False
    assert cfg["take_profit_enabled"] is False
    assert cfg["premium_stop_multiple"] == 2.5
    assert cfg["iv_warmup_enabled"] is False
    assert cfg["s1_falling_framework_enabled"] is False
    assert cfg["s1_forward_vega_filter_enabled"] is False
    assert cfg["portfolio_construction_enabled"] is False
