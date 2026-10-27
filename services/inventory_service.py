"""
Inventory Service: Normalization, Validation, and Reconciliation.

Handles SKU cleaning, numeric parsing, column resolution, multi-channel
reconciliation, and transformation of raw Shopify GraphQL responses.
"""

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


def clean_sku(series: Optional[pd.Series]) -> pd.Series:
    """
    Standardizes SKU strings: trims whitespace, removes trailing .0 from
    numeric conversions, removes control characters, and converts to uppercase.
    """
    if series is None:
        return pd.Series(dtype="string")
    s = series.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    s = s.str.replace(r"[\r\n\t]", "", regex=True)
    s = s.str.upper()
    return s.replace({"NAN": pd.NA, "NONE": pd.NA, "<NA>": pd.NA, "": pd.NA})


def to_num(series: Optional[pd.Series]) -> pd.Series:
    """
    Safely converts a series to numeric float, stripping currency symbols ($)
    and commas.
    """
    if series is None:
        return pd.Series(dtype=float)
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        cleaned = (
            series.astype(str)
            .str.replace("$", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        return pd.to_numeric(cleaned, errors="coerce")
    return pd.to_numeric(series, errors="coerce")


def find_col(df: Optional[pd.DataFrame], candidates: List[str]) -> Optional[str]:
    """Finds the first matching column name in df from a list of candidates."""
    if df is None:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def inventory_normalize(
    df: Optional[pd.DataFrame], source: str
) -> Tuple[Optional[pd.DataFrame], List[str]]:
    """
    Normalizes a single-channel inventory CSV DataFrame.
    Matches SKU and quantity columns, validates values, aggregates duplicates,
    and returns (normalized_df, warnings_or_errors).
    """
    if df is None:
        return None, []

    sku_col = find_col(
        df,
        [
            "sku", "variant_sku", "seller_sku", "product_sku", "item_sku",
            "item_number", "part_number", "barcode", "asin"
        ],
    )
    qty_col = find_col(
        df,
        [
            "stock", "quantity", "available", "available_quantity", "inventory",
            "qty", "units", "on_hand", "quantity_on_hand", "afn_fulfillable_quantity",
            "fulfillable_quantity", "variant_inventory_qty", "total_quantity", "current_stock"
        ],
    )

    if not sku_col or not qty_col:
        cols_found = ", ".join(list(df.columns)[:8]) if len(df.columns) > 0 else "none"
        return None, [
            f"{source}: required columns not found (found columns: [{cols_found}]). Need SKU and stock/quantity columns."
        ]

    out = pd.DataFrame({
        "sku": clean_sku(df[sku_col]),
        f"{source.lower()}_stock": to_num(df[qty_col]),
    })

    errors = []
    invalid = out["sku"].isna() | out["sku"].eq("") | out[f"{source.lower()}_stock"].isna()
    if invalid.any():
        errors.append(
            f"{source}: {int(invalid.sum())} row(s) have missing SKU or invalid/missing quantity (excluded from calculation)."
        )

    duplicates = out[out["sku"].duplicated(keep=False) & out["sku"].notna()]
    if not duplicates.empty:
        errors.append(
            f"{source}: {duplicates['sku'].nunique()} duplicated SKU(s) found; grouped by summed quantity for comparison."
        )

    out = out.dropna(subset=["sku", f"{source.lower()}_stock"])
    out = out[out["sku"].ne("")]
    out = out.groupby("sku", as_index=False)[f"{source.lower()}_stock"].sum()
    out[f"{source.lower()}_stock"] = out[f"{source.lower()}_stock"].astype(int)
    return out, errors


def reconcile_inventory(
    shopify_df: pd.DataFrame,
    amazon_df: pd.DataFrame,
    low_stock_threshold: int = 5,
) -> pd.DataFrame:
    """
    Performs full outer reconciliation between Shopify and Amazon inventory.
    Categorizes each SKU as:
    - 'Match': Quantities match exactly and stock > threshold
    - 'Low stock': Quantities match and stock <= threshold
    - 'Stock mismatch': SKU exists in both channels with different quantities
    - 'Missing from Amazon': SKU exists in Shopify only
    - 'Missing from Shopify': SKU exists in Amazon only
    """
    comparison = shopify_df.merge(amazon_df, on="sku", how="outer", indicator=True)
    comparison["shopify_stock"] = comparison["shopify_stock"].fillna(0).astype(int)
    comparison["amazon_stock"] = comparison["amazon_stock"].fillna(0).astype(int)
    comparison["stock_difference_shopify_minus_amazon"] = (
        comparison["shopify_stock"] - comparison["amazon_stock"]
    )

    comparison["status"] = "Match"
    comparison.loc[comparison["_merge"] == "left_only", "status"] = "Missing from Amazon"
    comparison.loc[comparison["_merge"] == "right_only", "status"] = "Missing from Shopify"
    comparison.loc[
        (comparison["_merge"] == "both")
        & (comparison["stock_difference_shopify_minus_amazon"] != 0),
        "status",
    ] = "Stock mismatch"

    low = comparison[["shopify_stock", "amazon_stock"]].min(axis=1).le(low_stock_threshold)
    comparison.loc[low & comparison["status"].eq("Match"), "status"] = "Low stock"

    comparison = comparison.drop(columns=["_merge"]).sort_values(["status", "sku"]).reset_index(drop=True)
    return comparison


def normalize_shopify_graphql_products(products_nodes: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Transforms raw Shopify Admin GraphQL product and variant nodes into a flat,
    normalized DataFrame with location-level inventory quantities.

    Expected structure per product node:
    - id, title, status
    - variants.nodes:
        - id, title, sku
        - inventoryItem:
            - id, tracked
            - inventoryLevels.nodes:
                - location: { id, name }
                - quantities: [{ name: 'available', quantity: int }]
    """
    rows = []
    for prod in products_nodes:
        p_id = prod.get("id", "")
        p_title = prod.get("title", "")
        p_status = prod.get("status", "")

        variants = prod.get("variants", {}).get("nodes", [])
        if not variants:
            # Product has no variants
            rows.append({
                "product_id": p_id,
                "product_title": p_title,
                "product_status": p_status,
                "variant_id": "",
                "variant_title": "",
                "sku": pd.NA,
                "inventory_item_id": "",
                "location_id": "",
                "location_name": "N/A",
                "available_quantity": 0,
                "inventory_tracked": False,
                "validation_status": "Missing variants",
            })
            continue

        for var in variants:
            v_id = var.get("id", "")
            v_title = var.get("title", "")
            raw_sku = var.get("sku")
            sku = str(raw_sku).strip().upper() if raw_sku else pd.NA

            inv_item = var.get("inventoryItem") or {}
            inv_item_id = inv_item.get("id", "")
            is_tracked = bool(inv_item.get("tracked", False))

            levels = inv_item.get("inventoryLevels", {}).get("nodes", [])
            if not levels:
                # Variant has no inventory level configured
                status_desc = "Untracked" if not is_tracked else "No inventory levels"
                if pd.isna(sku) or sku == "":
                    status_desc += " | Missing SKU"
                rows.append({
                    "product_id": p_id,
                    "product_title": p_title,
                    "product_status": p_status,
                    "variant_id": v_id,
                    "variant_title": v_title,
                    "sku": sku if pd.notna(sku) and sku != "" else pd.NA,
                    "inventory_item_id": inv_item_id,
                    "location_id": "",
                    "location_name": "Unassigned",
                    "available_quantity": 0,
                    "inventory_tracked": is_tracked,
                    "validation_status": status_desc,
                })
            else:
                for lvl in levels:
                    loc = lvl.get("location") or {}
                    loc_id = loc.get("id", "")
                    loc_name = loc.get("name") or (f"Location #{loc_id.split('/')[-1]}" if loc_id else "Default Location")

                    # Extract available quantity from quantities list
                    avail_qty = 0
                    quantities = lvl.get("quantities", [])
                    found_avail = False
                    for q in quantities:
                        if q.get("name") == "available":
                            avail_qty = q.get("quantity", 0)
                            found_avail = True
                            break
                    if not found_avail and "available" in lvl:
                        avail_qty = lvl.get("available", 0)

                    validation = "OK"
                    if pd.isna(sku) or sku == "":
                        validation = "Missing SKU"
                    elif not is_tracked:
                        validation = "Untracked"

                    rows.append({
                        "product_id": p_id,
                        "product_title": p_title,
                        "product_status": p_status,
                        "variant_id": v_id,
                        "variant_title": v_title,
                        "sku": sku if pd.notna(sku) and sku != "" else pd.NA,
                        "inventory_item_id": inv_item_id,
                        "location_id": loc_id,
                        "location_name": loc_name,
                        "available_quantity": int(avail_qty) if avail_qty is not None else 0,
                        "inventory_tracked": is_tracked,
                        "validation_status": validation,
                    })

    if not rows:
        return pd.DataFrame(columns=[
            "product_id", "product_title", "product_status", "variant_id",
            "variant_title", "sku", "inventory_item_id", "location_id",
            "location_name", "available_quantity", "inventory_tracked", "validation_status"
        ])

    df = pd.DataFrame(rows)
    df["available_quantity"] = df["available_quantity"].fillna(0).astype(int)
    return df
