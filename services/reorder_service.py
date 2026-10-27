"""
Reorder Service: Calculation and Operational Exception Logic.

Computes sales velocity, stock cover, supplier lead-time buffers, minimum order quantities (MOQ),
and generates actionable reorder recommendations with review statuses.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from services.inventory_service import clean_sku, find_col, to_num


def calculate_reorder_recommendations(
    inventory_df: pd.DataFrame,
    supplier_df: Optional[pd.DataFrame] = None,
    sales_df: Optional[pd.DataFrame] = None,
    default_lead_days: int = 14,
    safety_days: int = 30,
    default_low_stock_threshold: int = 5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Combines inventory quantities with supplier parameters and optional sales velocity.

    Returns:
        recommendations_df: Processed reorder suggestions.
        exceptions_df: Actionable exceptions (missing supplier mappings, missing parameters, etc.).
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if inventory_df is None or inventory_df.empty:
        empty_reorders = pd.DataFrame(columns=[
            "generated_at_utc", "sku", "product_title", "location",
            "available_quantity", "reorder_threshold", "target_stock",
            "supplier_name", "lead_time_days", "minimum_order_qty",
            "suggested_quantity", "reason", "review_status"
        ])
        empty_exceptions = pd.DataFrame(columns=[
            "timestamp_utc", "sku", "product_title", "exception_type",
            "severity", "details"
        ])
        return empty_reorders, empty_exceptions

    df = inventory_df.copy()

    # Identify stock and SKU columns
    sku_col = find_col(df, ["sku", "variant_sku", "seller_sku", "item_sku"])
    if not sku_col:
        raise ValueError("Inventory DataFrame is missing an SKU column.")
    df["sku"] = clean_sku(df[sku_col])

    qty_col = find_col(
        df,
        [
            "available_quantity", "shopify_stock", "stock", "quantity",
            "available", "current_stock", "on_hand"
        ],
    )
    if not qty_col:
        df["available_quantity"] = 0
    else:
        df["available_quantity"] = to_num(df[qty_col]).fillna(0).astype(int)

    # Standardize metadata columns if available
    if "product_title" not in df.columns:
        p_col = find_col(df, ["product_name", "title", "name", "item_name"])
        df["product_title"] = df[p_col] if p_col else df["sku"]
    if "location" not in df.columns:
        loc_col = find_col(df, ["location_name", "location_id", "warehouse", "store"])
        df["location"] = df[loc_col] if loc_col else "Default Location"

    # Merge sales velocity if provided
    if sales_df is not None and not sales_df.empty:
        s_df = sales_df.copy()
        s_sku = find_col(s_df, ["sku", "variant_sku", "seller_sku"])
        sold_c = find_col(s_df, ["units_sold", "quantity_sold", "units", "qty_sold", "sales", "sold"])
        days_c = find_col(s_df, ["period_days", "days", "days_covered", "lookback_days"])
        if s_sku and sold_c:
            s_df["sku"] = clean_sku(s_df[s_sku])
            s_df["units_sold"] = to_num(s_df[sold_c]).fillna(0.0).clip(lower=0)
            s_df["period_days"] = (
                to_num(s_df[days_c]).fillna(30.0).clip(lower=1.0) if days_c else 30.0
            )
            s_df["avg_daily_sales"] = (s_df["units_sold"] / s_df["period_days"]).fillna(0.0)
            df = df.merge(s_df[["sku", "avg_daily_sales", "units_sold"]].drop_duplicates("sku"), on="sku", how="left")
            df["avg_daily_sales"] = df["avg_daily_sales"].fillna(0.0)
        else:
            df["avg_daily_sales"] = 0.0
            df["units_sold"] = 0
    else:
        df["avg_daily_sales"] = 0.0
        df["units_sold"] = 0

    # Merge supplier master parameters if provided
    has_supplier = False
    if supplier_df is not None and not supplier_df.empty:
        sup = supplier_df.copy()
        sup_sku = find_col(sup, ["sku", "variant_sku", "seller_sku"])
        if sup_sku:
            sup["sku"] = clean_sku(sup[sup_sku])
            sup_name_col = find_col(sup, ["supplier_name", "supplier", "vendor"])
            lead_col = find_col(sup, ["lead_time_days", "lead_days", "lead_time", "leadtime"])
            moq_col = find_col(sup, ["minimum_order_qty", "moq", "min_order_qty"])
            thresh_col = find_col(sup, ["reorder_threshold", "threshold", "reorder_point"])
            target_col = find_col(sup, ["target_stock", "target_stock_units", "max_stock"])

            clean_sup = pd.DataFrame({"sku": sup["sku"]})
            clean_sup["supplier_name"] = sup[sup_name_col] if sup_name_col else "Default Supplier"
            clean_sup["lead_time_days"] = to_num(sup[lead_col]) if lead_col else pd.NA
            clean_sup["minimum_order_qty"] = to_num(sup[moq_col]) if moq_col else 1
            clean_sup["explicit_threshold"] = to_num(sup[thresh_col]) if thresh_col else pd.NA
            clean_sup["explicit_target_stock"] = to_num(sup[target_col]) if target_col else pd.NA

            clean_sup = clean_sup.drop_duplicates("sku")
            df = df.merge(clean_sup, on="sku", how="left")
            df["supplier_name"] = df["supplier_name"].fillna("Unassigned")
            has_supplier = True

    if not has_supplier:
        df["supplier_name"] = "Unassigned"
        df["lead_time_days"] = pd.NA
        df["minimum_order_qty"] = 1
        df["explicit_threshold"] = pd.NA
        df["explicit_target_stock"] = pd.NA

    # Fill defaults and compute reorder metrics
    df["lead_time_days"] = df["lead_time_days"].fillna(default_lead_days).astype(int)
    df["minimum_order_qty"] = df["minimum_order_qty"].fillna(1).clip(lower=1).astype(int)

    # Dynamic target stock: use explicit target stock if provided, otherwise sales velocity * safety_days
    # If no sales velocity, fallback to lead_time * 2 or low stock threshold * 3
    calculated_target = np.ceil(df["avg_daily_sales"] * safety_days)
    fallback_target = np.maximum(df["lead_time_days"] * 2, default_low_stock_threshold * 4)
    target = np.where(df["avg_daily_sales"] > 0, calculated_target, fallback_target)
    df["target_stock"] = np.where(
        df["explicit_target_stock"].notna() & (df["explicit_target_stock"] > 0),
        df["explicit_target_stock"],
        target,
    ).astype(int)

    # Reorder threshold: explicit threshold, or lead_time * avg_daily_sales, or fallback default threshold
    calculated_threshold = np.ceil(df["avg_daily_sales"] * df["lead_time_days"])
    reorder_point = np.where(df["avg_daily_sales"] > 0, calculated_threshold, default_low_stock_threshold)
    df["reorder_threshold"] = np.where(
        df["explicit_threshold"].notna() & (df["explicit_threshold"] >= 0),
        df["explicit_threshold"],
        reorder_point,
    ).astype(int)

    # Calculate suggested reorder quantity
    needed = df["target_stock"] - df["available_quantity"]
    raw_suggested = np.maximum(0, needed)
    # Apply MOQ if reorder needed
    moq_adjusted = np.where(
        raw_suggested > 0,
        np.maximum(raw_suggested, df["minimum_order_qty"]),
        0,
    )
    df["suggested_quantity"] = moq_adjusted.astype(int)

    # Evaluate reason and review status
    exceptions_list = []
    reasons = []
    statuses = []

    for _, row in df.iterrows():
        sku = row["sku"]
        stock = row["available_quantity"]
        thresh = row["reorder_threshold"]
        sup_name = row["supplier_name"]
        prod_title = str(row.get("product_title", sku))

        # Check for exceptions and validate SKU
        is_missing_sku = (
            pd.isna(sku)
            or str(sku).strip() == ""
            or str(sku).strip().lower() in ["nan", "none", "<na>", "null"]
        )

        if is_missing_sku:
            exceptions_list.append({
                "timestamp_utc": now_utc,
                "sku": "MISSING_SKU",
                "product_title": prod_title,
                "exception_type": "Missing SKU",
                "severity": "CRITICAL",
                "details": f"Product '{prod_title}' has no SKU assigned. Cannot be mapped to suppliers or reordered.",
            })
            reasons.append("Invalid or missing SKU")
            statuses.append("Error - Missing SKU")
        else:
            if sup_name == "Unassigned" and has_supplier:
                exceptions_list.append({
                    "timestamp_utc": now_utc,
                    "sku": str(sku).strip(),
                    "product_title": prod_title,
                    "exception_type": "Missing Supplier Mapping",
                    "severity": "WARNING",
                    "details": f"SKU {sku} has no matching entry in Supplier Master.",
                })

            if stock <= 0:
                msg = f"Out of stock ({stock} units on hand; threshold is {thresh})"
                if sup_name == "Unassigned" and has_supplier:
                    msg += " | Missing supplier mapping"
                reasons.append(msg)
                statuses.append("Urgent Reorder Required")
            elif stock <= thresh:
                if stock == thresh:
                    msg = f"Stock at reorder threshold ({stock} == {thresh})"
                else:
                    msg = f"Stock below reorder threshold ({stock} <= {thresh})"
                if sup_name == "Unassigned" and has_supplier:
                    msg += " | Missing supplier mapping"
                reasons.append(msg)
                statuses.append("Reorder Review Needed")
            else:
                msg = f"Healthy stock level ({stock} > threshold {thresh})"
                if sup_name == "Unassigned" and has_supplier:
                    msg += " | Missing supplier mapping"
                reasons.append(msg)
                statuses.append("Healthy")

    df["generated_at_utc"] = now_utc
    df["reason"] = reasons
    df["review_status"] = statuses

    df["suggested_reorder_qty"] = df["suggested_quantity"]
    df["reorder_now"] = df["review_status"].str.contains("Urgent|Reorder", na=False)

    # Exclude items with invalid/missing SKUs from reorder recommendations.
    # Products without SKUs cannot be ordered from suppliers and are routed
    # exclusively to the Exception Log as actionable exceptions.
    valid_sku_mask = ~df["review_status"].eq("Error - Missing SKU")
    valid_df = df[valid_sku_mask].copy()

    recommendations_df = valid_df[[
        "generated_at_utc", "sku", "product_title", "location",
        "available_quantity", "reorder_threshold", "target_stock",
        "supplier_name", "lead_time_days", "minimum_order_qty",
        "suggested_quantity", "suggested_reorder_qty", "reorder_now",
        "reason", "review_status"
    ]].sort_values(
        by=["suggested_quantity", "available_quantity"], ascending=[False, True]
    ).reset_index(drop=True)

    exceptions_df = (
        pd.DataFrame(exceptions_list)
        if exceptions_list
        else pd.DataFrame(columns=[
            "timestamp_utc", "sku", "product_title", "exception_type",
            "severity", "details"
        ])
    )

    return recommendations_df, exceptions_df
