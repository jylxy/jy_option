"""Broker-provided product margin ratios and option fee schedules.

The source files currently live at the workspace root:

- Exchange futures settlement margin query, dated 2026-04-24.
- Exchange option fee schedule, dated 2026-04-21.

The fee table contains separate speculative and hedge rows. S1 is modeled as a
speculative strategy, so this module intentionally uses the speculative rows.
"""

import re


BROKER_FUTURE_MARGIN_RATIO_BY_UNDERLYING = {
    "A": 0.10,
    "AD": 0.10,
    "AG": 0.16,
    "AL": 0.10,
    "AO": 0.10,
    "AP": 0.10,
    "AU": 0.16,
    "B": 0.10,
    "BB": 0.15,
    "BC": 0.10,
    "BR": 0.14,
    "BU": 0.14,
    "BZ": 0.20,
    "C": 0.10,
    "CF": 0.10,
    "CJ": 0.15,
    "CS": 0.10,
    "CU": 0.10,
    "CY": 0.10,
    "EB": 0.20,
    "EC": 0.30,
    "EG": 0.20,
    "FB": 0.10,
    "FG": 0.12,
    "FU": 0.22,
    "HC": 0.10,
    "I": 0.11,
    "IC": 0.12,
    "IF": 0.12,
    "IH": 0.12,
    "IM": 0.12,
    "J": 0.20,
    "JD": 0.20,
    "JM": 0.12,
    "JR": 0.15,
    "L": 0.07,
    "LC": 0.15,
    "LG": 0.10,
    "LH": 0.10,
    "LU": 0.22,
    "M": 0.10,
    "MA": 0.10,
    "NI": 0.10,
    "NR": 0.10,
    "OI": 0.10,
    "OP": 0.10,
    "P": 0.12,
    "PB": 0.10,
    "PD": 0.19,
    "PF": 0.10,
    "PG": 0.20,
    "PK": 0.10,
    "PL": 0.10,
    "PM": 0.15,
    "PP": 0.07,
    "PR": 0.10,
    "PS": 0.13,
    "PT": 0.19,
    "PX": 0.15,
    "RB": 0.10,
    "RI": 0.15,
    "RM": 0.10,
    "RR": 0.10,
    "RS": 0.20,
    "RU": 0.10,
    "SA": 0.10,
    "SC": 0.22,
    "SF": 0.10,
    "SH": 0.10,
    "SI": 0.10,
    "SM": 0.10,
    "SN": 0.13,
    "SP": 0.10,
    "SR": 0.10,
    "SS": 0.10,
    "T": 0.02,
    "TA": 0.10,
    "TF": 0.012,
    "TL": 0.035,
    "TS": 0.005,
    "UR": 0.10,
    "V": 0.07,
    "WH": 0.15,
    "WR": 0.10,
    "Y": 0.10,
    "ZC": 0.50,
    "ZN": 0.10,
}

INDEX_OPTION_TO_FUTURE_ROOT = {
    "HO": "IH",
    "IO": "IF",
    "MO": "IM",
}

ETF_OPTION_MARGIN_RATIO_BY_PRODUCT = {
    "510050": 0.12,
    "510300": 0.12,
    "510500": 0.12,
    "159915": 0.12,
    "159919": 0.12,
    "588000": 0.12,
}

BROKER_OPTION_FEE_BY_CODE = {
    "AD_O": {"open": 5.0, "close": 5.0, "close_today": 0.0, "exercise": 5.0, "assign": 0.0},
    "AG_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 0.0},
    "AL_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "AO_O": {"open": 3.5, "close": 3.5, "close_today": 0.0, "exercise": 3.5, "assign": 0.0},
    "APC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "APP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "AU_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 0.0},
    "A_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "BC_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 0.0},
    "BR_O": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "BU_O": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "BZ_O": {"open": 1.0, "close": 1.0, "close_today": 1.0, "exercise": 1.0, "assign": 1.0},
    "B_O": {"open": 0.2, "close": 0.2, "close_today": 0.2, "exercise": 0.5, "assign": 0.5},
    "CFC": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "CFP": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "CJC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "CJP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "CS_O": {"open": 0.2, "close": 0.2, "close_today": 0.2, "exercise": 0.2, "assign": 0.2},
    "CU_O": {"open": 5.0, "close": 5.0, "close_today": 0.0, "exercise": 5.0, "assign": 0.0},
    "C_O": {"open": 0.6, "close": 0.6, "close_today": 0.6, "exercise": 0.6, "assign": 0.6},
    "EB_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "EG_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "FGC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "FGP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "FU_O": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "HO": {"open": 15.0, "close": 15.0, "close_today": 15.0, "exercise": 1.0, "assign": 0.0},
    "IO": {"open": 15.0, "close": 15.0, "close_today": 15.0, "exercise": 1.0, "assign": 0.0},
    "I_O": {"open": 2.0, "close": 2.0, "close_today": 2.0, "exercise": 2.0, "assign": 2.0},
    "JD_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 0.5, "assign": 0.5},
    "JM_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 0.5, "assign": 0.5},
    "LC_O": {"open": 3.0, "close": 3.0, "close_today": 0.0, "exercise": 3.0, "assign": 3.0},
    "LG_O": {"open": 1.0, "close": 1.0, "close_today": 1.0, "exercise": 1.0, "assign": 1.0},
    "LH_O": {"open": 1.5, "close": 1.5, "close_today": 1.5, "exercise": 1.5, "assign": 1.5},
    "L_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "MAC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "MAP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "MO": {"open": 15.0, "close": 15.0, "close_today": 15.0, "exercise": 1.0, "assign": 0.0},
    "M_O": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 1.0},
    "NI_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "NR_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "OIC": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "OIP": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "OP_O": {"open": 5.0, "close": 5.0, "close_today": 0.0, "exercise": 5.0, "assign": 0.0},
    "PB_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "PD_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 2.0},
    "PFC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "PFP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "PG_O": {"open": 1.0, "close": 1.0, "close_today": 1.0, "exercise": 1.0, "assign": 1.0},
    "PKC": {"open": 0.8, "close": 0.8, "close_today": 0.0, "exercise": 0.8, "assign": 0.0},
    "PKP": {"open": 0.8, "close": 0.8, "close_today": 0.0, "exercise": 0.8, "assign": 0.0},
    "PLC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "PLP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "PP_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "PRC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "PRP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "PS_O": {"open": 2.0, "close": 2.0, "close_today": 2.0, "exercise": 2.0, "assign": 2.0},
    "PT_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 2.0},
    "PXC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "PXP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "P_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "RB_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "RMC": {"open": 0.8, "close": 0.8, "close_today": 0.0, "exercise": 0.8, "assign": 0.0},
    "RMP": {"open": 0.8, "close": 0.8, "close_today": 0.0, "exercise": 0.8, "assign": 0.0},
    "RU_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "SAC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SAP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SC_O": {"open": 10.0, "close": 10.0, "close_today": 0.0, "exercise": 10.0, "assign": 0.0},
    "SFC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SFP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SHC": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 0.0},
    "SHP": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 0.0},
    "SI_O": {"open": 2.0, "close": 2.0, "close_today": 0.0, "exercise": 2.0, "assign": 2.0},
    "SMC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SMP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "SN_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "SP_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "SRC": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "SRP": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
    "TAC": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "TAP": {"open": 0.5, "close": 0.5, "close_today": 0.0, "exercise": 0.5, "assign": 0.0},
    "URC": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "URP": {"open": 1.0, "close": 1.0, "close_today": 0.0, "exercise": 1.0, "assign": 0.0},
    "V_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "Y_O": {"open": 0.5, "close": 0.5, "close_today": 0.5, "exercise": 1.0, "assign": 1.0},
    "ZCC": {"open": 150.0, "close": 150.0, "close_today": 150.0, "exercise": 150.0, "assign": 0.0},
    "ZCP": {"open": 150.0, "close": 150.0, "close_today": 150.0, "exercise": 150.0, "assign": 0.0},
    "ZN_O": {"open": 1.5, "close": 1.5, "close_today": 0.0, "exercise": 1.5, "assign": 0.0},
}


def normalize_product_root(product):
    text = str(product or "").strip().upper()
    if not text:
        return ""
    text = text.split(".", 1)[0]
    if text.isdigit():
        return text
    match = re.match(r"([A-Z]+)", text)
    return match.group(1) if match else text


def broker_margin_ratio_for_product(product):
    root = normalize_product_root(product)
    if not root:
        return None
    if root in ETF_OPTION_MARGIN_RATIO_BY_PRODUCT:
        return ETF_OPTION_MARGIN_RATIO_BY_PRODUCT[root]
    future_root = INDEX_OPTION_TO_FUTURE_ROOT.get(root, root)
    return BROKER_FUTURE_MARGIN_RATIO_BY_UNDERLYING.get(future_root)


def _coerce_fee(value):
    try:
        fee = float(value)
    except (TypeError, ValueError):
        return None
    return fee if fee >= 0 else None


def _normalize_action(action):
    text = str(action or "open").strip().lower()
    aliases = {
        "open": "open",
        "open_sell": "open",
        "open_buy": "open",
        "close": "close",
        "close_sell": "close",
        "close_buy": "close",
        "close_today": "close_today",
        "today": "close_today",
        "exercise": "exercise",
        "execute": "exercise",
        "assign": "assign",
        "assignment": "assign",
    }
    return aliases.get(text, text)


def option_fee_code_candidates(product, option_type=None):
    root = normalize_product_root(product)
    side = str(option_type or "").strip().upper()[:1]
    if not root:
        return []
    candidates = []
    if side in {"C", "P"}:
        candidates.append(f"{root}{side}")
    candidates.append(root)
    candidates.append(f"{root}_O")
    return candidates


def _lookup_fee_override(mapping, product, option_type, action):
    if not mapping:
        return None
    root = normalize_product_root(product)
    side = str(option_type or "").strip().upper()[:1]
    keys = [
        f"{root}:{side}" if side else "",
        f"{root}_{side}" if side else "",
        f"{root}{side}" if side else "",
        root,
    ]
    upper_map = {str(k).upper(): v for k, v in mapping.items()}
    for key in keys:
        if not key:
            continue
        value = upper_map.get(key.upper())
        if value is None:
            continue
        if isinstance(value, dict):
            action_value = value.get(action)
            if action_value is None and action == "close_today":
                action_value = value.get("close")
            if action_value is None:
                action_value = value.get("open")
            return _coerce_fee(action_value)
        return _coerce_fee(value)
    return None


def resolve_option_fee(config=None, product=None, option_type=None,
                       action="open", default=None):
    """Resolve one-contract option fee for an action.

    ``fee`` remains the scalar fallback for products not covered by the broker
    table, but covered products use the product-level schedule by default.
    """
    cfg = config or {}
    action = _normalize_action(action)

    fee = _lookup_fee_override(
        cfg.get("option_fee_by_product_side", {}),
        product,
        option_type,
        action,
    )
    if fee is not None:
        return fee
    fee = _lookup_fee_override(
        cfg.get("option_fee_by_product", {}),
        product,
        option_type,
        action,
    )
    if fee is not None:
        return fee

    if cfg.get("option_fee_use_broker_table", True):
        for code in option_fee_code_candidates(product, option_type):
            schedule = BROKER_OPTION_FEE_BY_CODE.get(code)
            if not schedule:
                continue
            fee = _coerce_fee(schedule.get(action))
            if fee is None and action == "close_today":
                fee = _coerce_fee(schedule.get("close"))
            if fee is None:
                fee = _coerce_fee(schedule.get("open"))
            if fee is not None:
                return fee

    fee = _coerce_fee(default)
    if fee is not None:
        return fee
    fee = _coerce_fee(cfg.get("fee", 0.0))
    return fee if fee is not None else 0.0


def resolve_option_roundtrip_fee(config=None, product=None, option_type=None):
    return (
        resolve_option_fee(config, product, option_type, action="open")
        + resolve_option_fee(config, product, option_type, action="close")
    )
