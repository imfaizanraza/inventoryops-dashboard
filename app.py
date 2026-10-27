import io
import math
import os
from datetime import date, datetime, timezone
import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Load local environment configuration from .env if present
load_dotenv(override=True)

from integrations.shopify_client import ShopifyClient, ShopifyAPIError
from integrations.google_sheets_client import GoogleSheetsClient, GoogleSheetsAPIError
from services.inventory_service import (
    clean_sku,
    to_num,
    find_col,
    inventory_normalize,
    reconcile_inventory,
)
from services.reorder_service import calculate_reorder_recommendations
from services.sync_service import SyncService

st.set_page_config(page_title="InventoryOps Dashboard", page_icon="📦", layout="wide")

st.title("📦 InventoryOps: E-commerce Operations Dashboard")
st.caption("Reconcile inventory across channels, inspect fulfillment gaps, plan supplier reorders, and automate synchronization.")

# Initialize session state for API data and sync results
if "shopify_api_inventory" not in st.session_state:
    st.session_state["shopify_api_inventory"] = None
if "last_sync_result" not in st.session_state:
    st.session_state["last_sync_result"] = None
if "shopify_test_result" not in st.session_state:
    st.session_state["shopify_test_result"] = None
if "sheets_test_result" not in st.session_state:
    st.session_state["sheets_test_result"] = None

# Sidebar configuration and data source selector
with st.sidebar:
    st.header("⚙️ Configuration")
    data_mode = st.radio(
        "Data Source Mode:",
        ["Upload CSV", "Demo data", "Shopify API", "Shopify + Google Sheets"],
        index=0,
        help="Select between manual CSV uploads, sample data, read-only Shopify API, or full Shopify + Google Sheets automation.",
    )

    st.markdown("---")
    st.subheader("Inventory & Reorder Rules")
    low_stock_threshold = st.number_input(
        "Low-stock threshold (units)", min_value=0, max_value=100000, value=5, step=1
    )
    default_lead_days = st.number_input(
        "Default supplier lead time (days)", min_value=1, max_value=365, value=14, step=1
    )
    safety_days = st.number_input(
        "Target stock cover (days)", min_value=1, max_value=365, value=30, step=1
    )

    st.markdown("---")
    st.subheader("Integration Status")
    shopify_client = ShopifyClient()
    sheets_client = GoogleSheetsClient()

    if shopify_client.is_configured():
        st.success(f"Shopify: Configured\n`{shopify_client.store_domain}`")
    else:
        st.caption("Shopify: ⚪ Not configured in `.env`")

    if sheets_client.is_configured():
        st.success("Google Sheets: Configured")
    else:
        st.caption("Google Sheets: ⚪ Not configured in `.env`")


def read_csv(uploaded, label):
    """
    Safely reads an uploaded CSV file supporting multiple encodings,
    automatic delimiter detection, and clean column normalization.
    """
    if uploaded is None:
        return None
    try:
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        try:
            df = pd.read_csv(uploaded, encoding="utf-8-sig")
        except (UnicodeDecodeError, pd.errors.ParserError):
            if hasattr(uploaded, "seek"):
                uploaded.seek(0)
            try:
                df = pd.read_csv(uploaded, encoding="latin1")
            except Exception:
                if hasattr(uploaded, "seek"):
                    uploaded.seek(0)
                df = pd.read_csv(uploaded, sep=None, engine="python", encoding="latin1")

        if df is None or df.empty:
            st.warning(f"{label}: Uploaded file is empty.")
            return None

        df.columns = [
            str(c).strip().lstrip("\ufeff").lower().replace(" ", "_").replace("-", "_")
            for c in df.columns
        ]
        return df
    except Exception as e:
        st.error(f"Could not read {label}: {e}")
        return None


def make_csv_download(df):
    return df.to_csv(index=False).encode("utf-8")


tabs = st.tabs([
    "📊 Overview",
    "📦 Stock comparison",
    "🚚 Orders & delivery",
    "🧾 Supplier reordering",
    "📚 SOP & data guide",
])

# ------------------------------------------------------------------------------
# TAB 0: OVERVIEW & SYNCHRONIZATION CONTROLS
# ------------------------------------------------------------------------------
with tabs[0]:
    sample_dir = os.path.join(os.path.dirname(__file__), "sample_data")

    # If Demo Data mode is selected, load files directly from sample_data/
    if data_mode == "Demo data":
        st.info("💡 **Demo data mode active:** Sample CSV files are loaded automatically from `sample_data/`.")
        shopify_raw = read_csv(os.path.join(sample_dir, "shopify_inventory.csv"), "Shopify inventory")
        amazon_raw = read_csv(os.path.join(sample_dir, "amazon_inventory.csv"), "Amazon inventory")
        orders_raw = read_csv(os.path.join(sample_dir, "orders.csv"), "orders")
        sales_raw = read_csv(os.path.join(sample_dir, "sales_history.csv"), "sales history")
        suppliers_raw = read_csv(os.path.join(sample_dir, "suppliers.csv"), "suppliers")

        # Provide dummy uploaders so UI components and AppTests stay compatible
        with st.expander("Optional: Override demo files with custom CSVs", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.file_uploader("Shopify inventory CSV", type=["csv"], key="shopify")
                st.file_uploader("Amazon inventory CSV", type=["csv"], key="amazon")
            with c2:
                st.file_uploader("Orders / fulfilment CSV (optional)", type=["csv"], key="orders")
                st.file_uploader("Sales history CSV (optional)", type=["csv"], key="sales")
                st.file_uploader("Supplier / purchasing CSV (optional)", type=["csv"], key="suppliers")

    elif data_mode in ["Shopify API", "Shopify + Google Sheets"]:
        # API Operations Center
        st.subheader("🔌 API Operations & Sync Center")

        if not shopify_client.is_configured():
            st.warning(
                "⚠️ **Shopify credentials not found in `.env`**\n\n"
                "To connect your Shopify development store, add the following variables to your local `.env` file:\n"
                "- `SHOPIFY_STORE_DOMAIN=your-store.myshopify.com`\n"
                "- `SHOPIFY_CLIENT_ID=your_client_id`\n"
                "- `SHOPIFY_CLIENT_SECRET=your_client_secret`\n\n"
                "*(You can switch to **Upload CSV** or **Demo data** mode in the sidebar at any time without credentials.)*"
            )

        api_col1, api_col2 = st.columns(2)
        with api_col1:
            st.markdown("#### 1. Shopify GraphQL Admin API")
            s_btn_c1, s_btn_c2 = st.columns(2)
            with s_btn_c1:
                if st.button("🔍 Test Shopify Connection", use_container_width=True):
                    with st.spinner("Connecting to Shopify..."):
                        try:
                            res = shopify_client.test_connection()
                            st.session_state["shopify_test_result"] = res
                            st.success(f"Connected to **{res['shop_name']}** ({res['domain']})")
                        except Exception as e:
                            st.error(f"Shopify test failed: {e}")
            with s_btn_c2:
                if st.button("📥 Fetch Shopify Inventory", use_container_width=True):
                    with st.spinner("Querying product variants & locations via GraphQL..."):
                        try:
                            fetched_df = shopify_client.fetch_inventory()
                            st.session_state["shopify_api_inventory"] = fetched_df
                            st.success(f"Fetched {len(fetched_df)} inventory records from Shopify.")
                        except Exception as e:
                            st.error(f"Failed to fetch Shopify inventory: {e}")

            if st.session_state["shopify_api_inventory"] is not None:
                api_inv = st.session_state["shopify_api_inventory"]
                st.caption(f"Cached Shopify API data: **{len(api_inv)}** items across **{api_inv['location_name'].nunique()}** location(s).")
                st.dataframe(api_inv.head(5), use_container_width=True, hide_index=True)

        with api_col2:
            st.markdown("#### 2. Google Sheets Automation")
            if not sheets_client.is_configured():
                st.info(
                    "Google Sheets credentials (`GOOGLE_SERVICE_ACCOUNT_FILE` and `GOOGLE_SHEET_ID`) "
                    "are not yet configured. Output will be displayed locally."
                )

            g_btn_c1, g_btn_c2 = st.columns(2)
            with g_btn_c1:
                if st.button("📊 Test Sheets Connection", use_container_width=True):
                    with st.spinner("Connecting to Google Sheets..."):
                        try:
                            res = sheets_client.test_connection()
                            st.session_state["sheets_test_result"] = res
                            st.success(f"Connected to **{res['title']}**")
                            if res["missing_tabs"]:
                                st.warning(f"Missing required tabs: {', '.join(res['missing_tabs'])}")
                        except Exception as e:
                            st.error(f"Google Sheets test failed: {e}")
            with g_btn_c2:
                if st.button("🚀 Run Sync to Sheets", use_container_width=True):
                    with st.spinner("Synchronizing Shopify inventory to Google Sheets..."):
                        sync_svc = SyncService(shopify_client=shopify_client, sheets_client=sheets_client)
                        result = sync_svc.run_sync(publish_to_sheets=sheets_client.is_configured(), trigger="dashboard")
                        st.session_state["last_sync_result"] = result
                        if result["status"] in ["SUCCESS", "PREVIEW_OK"]:
                            st.success(f"Sync completed! Read: {result['records_read']} | Written: {result['records_written']}")
                        else:
                            st.error(f"Sync status: {result['status']}. {result['error_summary']}")

            if st.session_state["last_sync_result"] is not None:
                sr = st.session_state["last_sync_result"]
                m_a, m_b, m_c = st.columns(3)
                m_a.metric("Last Run Status", sr["status"])
                m_b.metric("Records Read", sr["records_read"])
                m_c.metric("Records Written", sr["records_written"])
                if sr.get("error_summary"):
                    st.caption(f"Notice: {sr['error_summary']}")

        st.markdown("---")
        st.subheader("Channel CSV Uploads (Amazon / Orders / Sales)")
        c1, c2 = st.columns(2)
        with c1:
            shopify_file = st.file_uploader("Shopify inventory CSV (Optional override)", type=["csv"], key="shopify")
            amazon_file = st.file_uploader("Amazon inventory CSV", type=["csv"], key="amazon")
        with c2:
            orders_file = st.file_uploader("Orders / fulfilment CSV (optional)", type=["csv"], key="orders")
            sales_file = st.file_uploader("Sales history CSV (optional)", type=["csv"], key="sales")
            suppliers_file = st.file_uploader("Supplier / purchasing CSV (optional)", type=["csv"], key="suppliers")

        shopify_raw = read_csv(shopify_file, "Shopify inventory")
        # If no CSV uploaded but Shopify API inventory is cached, use normalized API inventory!
        if shopify_raw is None and st.session_state["shopify_api_inventory"] is not None:
            shopify_raw = st.session_state["shopify_api_inventory"].rename(
                columns={"available_quantity": "stock"}
            )

        amazon_raw = read_csv(amazon_file, "Amazon inventory")
        orders_raw = read_csv(orders_file, "orders")
        sales_raw = read_csv(sales_file, "sales history")
        suppliers_raw = read_csv(suppliers_file, "suppliers")

    else:
        # Standard Upload CSV Mode
        st.subheader("Upload your exports")
        c1, c2 = st.columns(2)
        with c1:
            shopify_file = st.file_uploader("Shopify inventory CSV", type=["csv"], key="shopify")
            amazon_file = st.file_uploader("Amazon inventory CSV", type=["csv"], key="amazon")
        with c2:
            orders_file = st.file_uploader("Orders / fulfilment CSV (optional)", type=["csv"], key="orders")
            sales_file = st.file_uploader("Sales history CSV (optional)", type=["csv"], key="sales")
            suppliers_file = st.file_uploader("Supplier / purchasing CSV (optional)", type=["csv"], key="suppliers")

        shopify_raw = read_csv(shopify_file, "Shopify inventory")
        amazon_raw = read_csv(amazon_file, "Amazon inventory")
        orders_raw = read_csv(orders_file, "orders")
        sales_raw = read_csv(sales_file, "sales history")
        suppliers_raw = read_csv(suppliers_file, "suppliers")

    # File / Data source status summary
    st.markdown("#### Data Sources Status")
    status_cols = st.columns(5)
    files_info = [
        ("Shopify Inventory", shopify_raw),
        ("Amazon Inventory", amazon_raw),
        ("Orders / Fulfilment", orders_raw),
        ("Sales History", sales_raw),
        ("Suppliers", suppliers_raw),
    ]
    for col, (label, raw_df) in zip(status_cols, files_info):
        with col:
            if raw_df is not None:
                st.success(f"**{label}**\n\n✅ {len(raw_df)} rows")
            else:
                st.caption(f"**{label}**\n\n⚪ Not loaded")

    st.markdown("---")
    files_loaded = sum(x is not None for x in [shopify_raw, amazon_raw, orders_raw, sales_raw, suppliers_raw])
    m1, m2, m3 = st.columns(3)
    m1.metric("Active data sets", files_loaded)
    m2.metric("Inventory channels", sum(x is not None for x in [shopify_raw, amazon_raw]))
    m3.metric("Operating Mode", data_mode)


# ------------------------------------------------------------------------------
# TAB 1: STOCK COMPARISON & RECONCILIATION
# ------------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Inventory reconciliation")
    if shopify_raw is None and amazon_raw is None:
        st.info("ℹ️ Load at least one inventory source in the **Overview** tab. Load both to compare stock between channels.")
    else:
        sh, sh_err = inventory_normalize(shopify_raw, "Shopify")
        am, am_err = inventory_normalize(amazon_raw, "Amazon")
        for msg in sh_err + am_err:
            st.warning(msg)

        if sh is not None and am is not None:
            comparison = reconcile_inventory(sh, am, low_stock_threshold=low_stock_threshold)

            a, b, c, d = st.columns(4)
            a.metric("SKUs compared", len(comparison))
            b.metric("Stock mismatches", int(comparison["status"].eq("Stock mismatch").sum()))
            c.metric("Missing records", int(comparison["status"].str.startswith("Missing").sum()))
            d.metric("Low stock / at threshold", int(comparison["status"].eq("Low stock").sum()))
            st.dataframe(comparison, width="stretch", hide_index=True)
            st.download_button(
                "Download stock comparison CSV",
                make_csv_download(comparison),
                "stock_comparison.csv",
                "text/csv",
            )
        elif sh is not None:
            st.info("💡 Shopify inventory loaded. Upload Amazon inventory in the Overview tab to enable side-by-side reconciliation.")
            sh_display = sh.copy()
            sh_display["status"] = "OK"
            sh_display.loc[sh_display["shopify_stock"] <= low_stock_threshold, "status"] = "Low stock"
            a, b, c = st.columns(3)
            a.metric("Total SKUs", len(sh_display))
            a_sum = int(sh_display["shopify_stock"].sum())
            b.metric("Total Shopify Units", a_sum)
            c.metric("Low Stock SKUs", int(sh_display["status"].eq("Low stock").sum()))
            st.dataframe(sh_display, width="stretch", hide_index=True)
            st.download_button(
                "Download normalized Shopify inventory CSV",
                make_csv_download(sh_display),
                "shopify_inventory_normalized.csv",
                "text/csv",
            )
        elif am is not None:
            st.info("💡 Amazon inventory loaded. Upload Shopify inventory in the Overview tab to enable side-by-side reconciliation.")
            am_display = am.copy()
            am_display["status"] = "OK"
            am_display.loc[am_display["amazon_stock"] <= low_stock_threshold, "status"] = "Low stock"
            a, b, c = st.columns(3)
            a.metric("Total SKUs", len(am_display))
            b.metric("Total Amazon Units", int(am_display["amazon_stock"].sum()))
            c.metric("Low Stock SKUs", int(am_display["status"].eq("Low stock").sum()))
            st.dataframe(am_display, width="stretch", hide_index=True)
            st.download_button(
                "Download normalized Amazon inventory CSV",
                make_csv_download(am_display),
                "amazon_inventory_normalized.csv",
                "text/csv",
            )
        else:
            st.error("Could not parse inventory columns. Please verify that your CSV contains SKU and quantity columns.")


# ------------------------------------------------------------------------------
# TAB 2: ORDERS & DELIVERY CHECKS
# ------------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Orders and delivery checks")
    if orders_raw is None:
        st.info("Upload an orders/fulfilment CSV in the Overview tab.")
    else:
        df = orders_raw.copy()
        order_col = find_col(df, [
            "order_id", "order_number", "order_no", "order", "order_name",
            "name", "id", "amazon_order_id", "merchant_order_id", "sales_order_number",
            "order_reference", "invoice_no"
        ])
        track_col = find_col(df, [
            "tracking_number", "tracking", "tracking_id", "tracking_code",
            "tracking_no", "tracking_url", "carrier_tracking", "waybill",
            "tracking_num", "shipment_tracking_number"
        ])
        status_col = find_col(df, [
            "fulfillment_status", "fulfilment_status", "order_status", "status",
            "shipping_status", "delivery_status", "shipment_status", "state"
        ])
        sku_col = find_col(df, ["sku", "seller_sku", "product_sku", "item_sku", "variant_sku"])

        if not order_col:
            st.warning("Could not identify an order ID column. Recognised: order_id, order_number, order_no, name, id, amazon_order_id.")
            st.dataframe(df, width="stretch", hide_index=True)
        else:
            df["order_check"] = "OK"
            if track_col:
                missing_tracking = df[track_col].isna() | df[track_col].astype(str).str.strip().isin(["", "nan", "None", "<NA>"])
                if status_col:
                    unfulfilled = df[status_col].astype(str).str.lower().str.contains("unfulfilled|pending|processing|open", regex=True, na=False)
                    df.loc[missing_tracking & ~unfulfilled, "order_check"] = "Missing tracking"
                    df.loc[unfulfilled, "order_check"] = "Not yet fulfilled"
                else:
                    df.loc[missing_tracking, "order_check"] = "Missing tracking"
            else:
                st.warning("No tracking-number column detected; order IDs can be listed, but tracking checks are limited.")
                df["order_check"] = "Tracking column not found"

            # Check duplicate order rows without overwriting unfulfilled or missing tracking status
            dups = df[order_col].duplicated(keep=False)
            if dups.any():
                df.loc[dups & df["order_check"].eq("OK"), "order_check"] = "Duplicate order ID — review"
                needs_note = dups & df["order_check"].ne("OK") & ~df["order_check"].str.contains("Duplicate", na=False)
                df.loc[needs_note, "order_check"] = df.loc[needs_note, "order_check"] + " (Multi-item / duplicate ID)"

            m1, m2 = st.columns(2)
            m1.metric("Orders / rows", len(df))
            m2.metric("Rows needing review", int(df["order_check"].ne("OK").sum()))
            st.dataframe(df, width="stretch", hide_index=True)
            st.download_button(
                "Download order exception report",
                make_csv_download(df),
                "order_checks.csv",
                "text/csv",
            )


# ------------------------------------------------------------------------------
# TAB 3: SUPPLIER REORDERING
# ------------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Supplier reorder suggestions")

    # If in API mode and inventory is loaded, use calculate_reorder_recommendations
    reorder_inv_source = None
    if sales_raw is not None:
        reorder_inv_source = sales_raw
    elif shopify_raw is not None:
        sh_norm, _ = inventory_normalize(shopify_raw, "Shopify")
        if sh_norm is not None:
            reorder_inv_source = sh_norm.rename(columns={"shopify_stock": "current_stock"})

    # Determine supplier rules source (uploaded CSV or Google Sheets Supplier Master)
    active_supplier_source = suppliers_raw
    if active_supplier_source is None and data_mode == "Shopify + Google Sheets" and sheets_client.is_configured():
        try:
            active_supplier_source = sheets_client.read_supplier_master()
        except Exception:
            active_supplier_source = None

    if reorder_inv_source is None:
        st.info("Upload sales history or inventory in the Overview tab to calculate suggested reorder quantities.")
    else:
        recommendations, exceptions = calculate_reorder_recommendations(
            inventory_df=reorder_inv_source,
            supplier_df=active_supplier_source,
            sales_df=sales_raw,
            default_lead_days=default_lead_days,
            safety_days=safety_days,
            default_low_stock_threshold=low_stock_threshold,
        )

        st.info(
            "📋 **Operational Planning Notice:** Suggested reorder quantities are recommendations for purchasing review. "
            "**No purchase orders have been created or sent, and no store inventory has been altered.**"
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("SKUs Evaluated", len(recommendations))
        m2.metric("🟢 Healthy Stock", int(recommendations["review_status"].eq("Healthy").sum()))
        m3.metric("🟡 Review Needed", int(recommendations["review_status"].eq("Reorder Review Needed").sum()))
        m4.metric("🔴 Urgent Reorder", int(recommendations["review_status"].eq("Urgent Reorder Required").sum()))

        if not exceptions.empty:
            with st.expander(f"⚠️ Actionable Exceptions Detected ({len(exceptions)})", expanded=True):
                st.caption("These items have missing SKUs or lack a Supplier Master mapping. They are unassigned.")
                st.dataframe(exceptions, width="stretch", hide_index=True)

        # Order columns clearly while retaining legacy aliases for backward compatibility
        preferred_cols = [
            "review_status", "sku", "product_title", "supplier_name",
            "available_quantity", "reorder_threshold", "target_stock",
            "minimum_order_qty", "suggested_quantity", "reason",
            "lead_time_days", "location", "suggested_reorder_qty", "reorder_now"
        ]
        display_df = recommendations[[c for c in preferred_cols if c in recommendations.columns]]

        st.dataframe(display_df, width="stretch", hide_index=True)
        st.download_button(
            "Download reorder suggestions CSV",
            make_csv_download(recommendations),
            "reorder_suggestions.csv",
            "text/csv",
        )


# ------------------------------------------------------------------------------
# TAB 4: SOP & DATA GUIDE
# ------------------------------------------------------------------------------
with tabs[4]:
    st.subheader("SOP and data guide")
    st.markdown("""
### Operating Modes
1. **Upload CSV (Default):** Upload exports directly in your browser. All data stays local to your active session.
2. **Demo Data:** Immediately explore the 5 operational tasks using pre-packaged test data from `sample_data/`.
3. **Shopify API:** Direct read-only connection to your Shopify development store using the Admin GraphQL API.
4. **Shopify + Google Sheets:** End-to-end operational hub reading inventory from Shopify and synchronizing snapshots, recommendations, and audit logs to Google Sheets.

### Authoritative Data Boundaries
- **Shopify Admin API:** Authoritative for Products, Variants, SKUs, Inventory Locations, and Available Quantities.
- **Google Sheets:** Authoritative for human-maintained supplier rules (Supplier ID, MOQ, Lead Times, Target Stock).
- **Python / Helfen Hub:** Computes reconciliation, velocity, reorder quantities, and audit logs.

### Recognised CSV Columns

**Inventory:** `sku` / `variant_sku` / `seller_sku` and `stock` / `quantity` / `available` / `inventory` / `qty`.

**Orders:** `order_id` / `order_number` / `amazon_order_id`, plus `tracking_number` and optionally `fulfillment_status`.

**Sales:** `sku`, `units_sold`, optional `period_days`, and optional `current_stock`.

**Supplier:** `sku`, `supplier_name`, optional `lead_time_days`, and `minimum_order_qty`.
""")
    st.markdown("### Sample CSV templates")
    st.code("sku,stock\nWATCH-001,20\nWATCH-002,5\nWATCH-003,3", language="csv")
    st.code("order_id,tracking_number,fulfillment_status\n1001,TRACK123,fulfilled\n1002,,fulfilled\n1003,,unfulfilled", language="csv")
    st.code("sku,units_sold,period_days,current_stock\nWATCH-001,60,30,20\nWATCH-002,45,30,5\nWATCH-003,90,30,8", language="csv")
    st.code("sku,supplier_name,lead_time_days\nWATCH-001,Supplier A,10\nWATCH-002,Supplier B,21", language="csv")
