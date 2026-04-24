"""Product taxonomy helpers for portfolio concentration controls."""

PRODUCT_BUCKET_MAP = {
    "510050": "equity_core",
    "510300": "equity_core",
    "159919": "equity_core",
    "IO": "equity_core",
    "HO": "equity_core",
    "510500": "equity_mid",
    "MO": "equity_mid",
    "159915": "equity_growth",
    "588000": "equity_growth",
    "AU": "precious_metals",
    "AG": "precious_metals",
    "CU": "base_metals",
    "AL": "base_metals",
    "ZN": "base_metals",
    "NI": "base_metals",
    "SN": "base_metals",
    "PB": "base_metals",
    "I": "black_chain",
    "RB": "black_chain",
    "HC": "black_chain",
    "JM": "black_chain",
    "J": "black_chain",
    "SF": "black_chain",
    "SM": "black_chain",
    "BU": "energy_chem",
    "TA": "energy_chem",
    "MA": "energy_chem",
    "EG": "energy_chem",
    "PP": "energy_chem",
    "L": "energy_chem",
    "V": "energy_chem",
    "EB": "energy_chem",
    "RU": "energy_chem",
    "NR": "energy_chem",
    "M": "agri_oilseeds",
    "RM": "agri_oilseeds",
    "Y": "agri_oilseeds",
    "P": "agri_oilseeds",
    "OI": "agri_oilseeds",
    "A": "agri_oilseeds",
    "B": "agri_oilseeds",
    "C": "agri_grains",
    "CS": "agri_grains",
    "CF": "softs",
    "SR": "softs",
}

PRODUCT_CORR_GROUP_MAP = {
    "510050": "equity_cn_large",
    "510300": "equity_cn_large",
    "159919": "equity_cn_large",
    "IO": "equity_cn_large",
    "HO": "equity_cn_large",
    "510500": "equity_cn_mid",
    "MO": "equity_cn_mid",
    "159915": "equity_cn_growth",
    "588000": "equity_cn_growth",
    "AU": "metals_precious",
    "AG": "metals_precious",
    "CU": "metals_base",
    "AL": "metals_base",
    "ZN": "metals_base",
    "NI": "metals_base",
    "SN": "metals_base",
    "PB": "metals_base",
    "I": "black_chain",
    "RB": "black_chain",
    "HC": "black_chain",
    "JM": "black_chain",
    "J": "black_chain",
    "BU": "energy_chain",
    "TA": "energy_chain",
    "MA": "energy_chain",
    "EG": "energy_chain",
    "PP": "energy_chain",
    "L": "energy_chain",
    "V": "energy_chain",
    "EB": "energy_chain",
    "RU": "rubber_chain",
    "NR": "rubber_chain",
    "M": "oilseeds_meal",
    "RM": "oilseeds_meal",
    "Y": "oils",
    "P": "oils",
    "OI": "oils",
    "A": "grains_feed",
    "B": "grains_feed",
    "C": "grains_feed",
    "CS": "grains_feed",
    "CF": "softs",
    "SR": "softs",
}


def normalize_product_key(product):
    """Normalize product identifiers to the canonical engine key."""
    return str(product).upper().strip()


def get_product_bucket(product):
    """Return concentration bucket for a product."""
    key = normalize_product_key(product)
    return PRODUCT_BUCKET_MAP.get(key, f"other:{key}")


def get_product_corr_group(product):
    """Return correlation group for a product."""
    key = normalize_product_key(product)
    return PRODUCT_CORR_GROUP_MAP.get(key, f"idio:{key}")
