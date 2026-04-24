"""SQL filter builders shared by the minute backtest data loaders."""

from product_taxonomy import normalize_product_key


def quote_sql_literal(value):
    """Quote a value as a single SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def normalize_sql_values(values):
    """Return sorted unique non-empty SQL values."""
    normalized = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized.append(text)
    return sorted(set(normalized))


def iter_code_filter_sql(codes, column_name="ths_code", chunk_size=500):
    """Yield chunked IN filters for a list of codes."""
    normalized_codes = normalize_sql_values(codes)
    chunk_size = max(1, int(chunk_size or 1))
    for start in range(0, len(normalized_codes), chunk_size):
        chunk = normalized_codes[start:start + chunk_size]
        code_list = ", ".join(quote_sql_literal(code) for code in chunk)
        yield f"{column_name} IN ({code_list})"


def build_code_filter_sql(codes, column_name="ths_code", chunk_size=1000):
    """Build an OR-joined IN filter for code lists."""
    parts = list(iter_code_filter_sql(codes, column_name=column_name, chunk_size=chunk_size))
    if not parts:
        return None
    return " OR ".join(parts)


def normalize_product_pool(product_pool):
    """Normalize product pool values for stable cache keys and SQL output."""
    return tuple(sorted({
        normalize_product_key(product)
        for product in product_pool
        if product is not None and str(product).strip()
    }))


def build_product_like_sql(
    product_pool,
    contract_cache,
    product_code_lookup,
    column_name="ths_code",
    explicit_chunk_size=1000,
):
    """Build product filters using LIKE for futures-style roots and IN for ETF codes."""
    normalized_pool = normalize_product_pool(product_pool)
    if not normalized_pool:
        return None

    pool_set = set(normalized_pool)
    product_suffixes = {}
    explicit_chunks = []

    for code, info in contract_cache.items():
        root = info.get("product_root")
        if root not in pool_set:
            continue
        if str(root).isdigit() and len(str(root)) == 6:
            continue
        suffix = str(code).rsplit(".", 1)[-1] if "." in str(code) else ""
        if suffix and root not in product_suffixes:
            product_suffixes[root] = suffix

    for product in sorted({
        product for product in pool_set
        if product.isdigit() and len(product) == 6
    }):
        codes = sorted(product_code_lookup(product))
        explicit_chunks.extend(
            iter_code_filter_sql(codes, column_name=column_name, chunk_size=explicit_chunk_size)
        )

    parts = [
        f"{column_name} LIKE {quote_sql_literal(f'{root}%.{suffix}')}"
        for root, suffix in product_suffixes.items()
    ]
    parts.extend(explicit_chunks)
    if not parts:
        return None
    return " OR ".join(parts)
