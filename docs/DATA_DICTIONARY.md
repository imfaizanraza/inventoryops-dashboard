# Helfen Operations Hub — Data Dictionary & System Architecture

## 1. System Overview & Data Flow Architecture

The Helfen Operations Hub operates under a strictly partitioned data ownership architecture:

```
External Systems ──> API Connectors ──> Normalized Domain Layer ──> Operational Tasks ──> Outputs
[Shopify GraphQL]       [shopify_client]     [inventory_service]      [Reconciliation]     [Streamlit UI]
[Google Sheets]        [sheets_client]      [reorder_service]        [Fulfillment Audit]  [Google Sheets]
[Channel CSVs]         [read_csv / io]      [sync_service]           [Reorder Engine]     [CSV Downloads]
```

### Authoritative Source Principles
1. **Shopify Admin API is Authoritative** for Products, Variants, SKUs, Inventory Locations, and Available Quantities. Manual edits to inventory quantities in Google Sheets are never authoritative and will not overwrite Shopify.
2. **Google Sheets is Authoritative** for human-managed operational rules and supplier metadata (Supplier ID, MOQ, Lead Times, Target Stock Overrides, and Rule Notes).
3. **Python Application (Helfen Hub)** is the calculation and transformation engine that reconciles multi-channel balances, computes sales velocities, determines reorder quantities, and logs audit records.

---

## 2. Operational Tasks & Data Mappings

### Task 1: Single-Channel Inventory Normalization & Low-Stock Detection
- **Business Purpose:** Ingests inventory balances from a single channel (Shopify or Amazon), validates SKUs, sums duplicate SKU records, and flags items at or below safety stock.
- **Authoritative Source:** The source channel export (or Shopify Admin GraphQL API).
- **Canonical Schema:**
  - `sku` (string, uppercase, trimmed): Unique product identifier.
  - `<channel>_stock` (integer, >= 0): Available stock on hand.
  - `status` (string): `"OK"` or `"Low stock"`.
- **Validation Rules:**
  - Missing SKU or missing quantity drops the row from stock sums and triggers a warning badge.
  - Multiple entries for the same SKU are summed together rather than picking the first or last row.

---

### Task 2: Multi-Channel Cross-Reconciliation (Shopify vs. Amazon)
- **Business Purpose:** Detects channel drift, out-of-sync catalog items, and stock discrepancies between Shopify and Amazon.
- **Authoritative Sources:** Shopify Admin API / CSV (Shopify) and Amazon Seller Central CSV (Amazon).
- **Calculation Logic:** Full outer join on canonical `sku`:
  $$\text{stock\_difference} = \text{shopify\_stock} - \text{amazon\_stock}$$
- **Status Classification:**
  - `Match`: Both channels present, quantities identical, and stock > threshold.
  - `Stock mismatch`: Both channels present, but `stock_difference != 0`.
  - `Missing from Amazon`: Present in Shopify, missing in Amazon export.
  - `Missing from Shopify`: Present in Amazon, missing in Shopify export.
  - `Low stock`: Quantities match, but stock <= `low_stock_threshold`.

---

### Task 3: Orders & Fulfillment Exception Audit
- **Business Purpose:** Identifies customer orders that have been marked fulfilled without tracking numbers, unfulfilled orders awaiting processing, and duplicate order entries.
- **Input Source:** Order export (`orders.csv` or ERP order report).
- **Field Mappings:**
  - `order_id`: Recognizes aliases (`order_id`, `order_number`, `order_no`, `name`, `id`, `amazon_order_id`).
  - `tracking_number`: Recognizes aliases (`tracking_number`, `tracking`, `tracking_id`, `tracking_code`, `waybill`).
  - `fulfillment_status`: Recognizes aliases (`fulfillment_status`, `order_status`, `status`, `shipping_status`).
- **Audit Rules:**
  - If tracking number is empty and fulfillment status is `"unfulfilled"`, `"pending"`, `"processing"`, or `"open"` -> `"Not yet fulfilled"`.
  - If tracking number is empty and fulfillment status is `"fulfilled"` -> `"Missing tracking"`.
  - If `order_id` appears on multiple rows -> `"Duplicate order ID — review"` (or appended note for multi-item orders).

---

### Task 4: Supplier Reorder Recommendations Engine
- **Business Purpose:** Calculates suggested reorder quantities based on sales velocity, supplier lead time, minimum order quantities (MOQ), and safety cover.
- **Data Sources:**
  - Inventory Quantities: Shopify GraphQL API (or normalized inventory DataFrame).
  - Supplier Rules: Google Sheets `"Supplier Master"` tab (or `suppliers.csv`).
  - Sales Velocity: `sales_history.csv` (or default velocity assumptions).
- **Mathematical Formulas:**
  1. Average Daily Sales:
     $$\text{avg\_daily\_sales} = \frac{\text{units\_sold}}{\text{period\_days}}$$
  2. Target Stock Level:
     $$\text{target\_stock} = \begin{cases} \text{explicit\_target\_stock} & \text{if explicitly provided in Supplier Master} \\ \lceil \text{avg\_daily\_sales} \times \text{safety\_days} \rceil & \text{otherwise} \end{cases}$$
  3. Reorder Threshold / Point:
     $$\text{reorder\_threshold} = \begin{cases} \text{explicit\_threshold} & \text{if explicitly provided in Supplier Master} \\ \lceil \text{avg\_daily\_sales} \times \text{lead\_time\_days} \rceil & \text{otherwise} \end{cases}$$
  4. Suggested Reorder Quantity:
     $$\text{raw\_needed} = \max(0, \text{target\_stock} - \text{available\_quantity})$$
     $$\text{suggested\_quantity} = \begin{cases} \max(\text{raw\_needed}, \text{minimum\_order\_qty}) & \text{if } \text{raw\_needed} > 0 \\ 0 & \text{otherwise} \end{cases}$$
- **Review Urgency Classification:**
  - `Urgent Reorder Required`: Available quantity <= 0 (Stockout).
  - `Reorder Review Needed`: Available quantity <= `reorder_threshold`.
  - `Healthy`: Available quantity > `reorder_threshold`.
  - `Missing Supplier Mapping`: SKU has no entry in Supplier Master.

---

### Task 5: Operations SOP & Reference Guide
- Informs operations staff regarding column recognition aliases, encoding rules (UTF-8 with BOM, Latin-1, cp1252), and standard operating procedures for synchronizing data to Google Sheets.

---

## 3. Google Sheets Managed Schema Specification

The Google Spreadsheet (`Helfen Operations Data Hub`) uses 5 dedicated tabs:

### Tab 1: `Supplier Master` (Human Maintained Input)
| Column Name | Data Type | Description | Required? |
| :--- | :--- | :--- | :--- |
| `sku` | String | Unique product SKU | **Yes** |
| `supplier_id` | String | Internal vendor identifier | No |
| `supplier_name` | String | Name of supplier / vendor | **Yes** |
| `supplier_sku` | String | Vendor's catalog SKU | No |
| `lead_time_days` | Integer | Shipping & production lead time in days | **Yes** (Default: 14) |
| `minimum_order_qty` | Integer | Supplier Minimum Order Quantity (MOQ) | **Yes** (Default: 1) |
| `reorder_threshold` | Integer | Manual reorder trigger point | Optional |
| `target_stock` | Integer | Target inventory ceiling | Optional |
| `notes` | String | Packaging, terms, or contact notes | No |
| `updated_at` | String | Date of last supplier term update | No |

### Tab 2: `Inventory Snapshot` (Generated from Shopify GraphQL API)
*Update Strategy: Deterministic overwrite (Clear range A1:Z then write fresh snapshot)*
- `sync_timestamp_utc`: ISO UTC timestamp of sync run.
- `product_id`: Shopify GraphQL GID (`gid://shopify/Product/...`).
- `product_title`: Product display title.
- `variant_id`: Shopify Variant GID (`gid://shopify/ProductVariant/...`).
- `sku`: Variant SKU.
- `inventory_item_id`: Inventory Item GID (`gid://shopify/InventoryItem/...`).
- `location`: Warehouse / Store location name.
- `available_quantity`: Available stock units on hand.
- `inventory_tracked`: Boolean (`True`/`False`).
- `validation_status`: Validation status (`OK`, `Untracked`, `Missing SKU`).

### Tab 3: `Reorder Recommendations` (Generated Calculations)
*Update Strategy: Deterministic overwrite*
- `generated_at_utc`, `sku`, `product_title`, `location`, `available_quantity`, `reorder_threshold`, `target_stock`, `supplier_name`, `lead_time_days`, `minimum_order_qty`, `suggested_quantity`, `reason`, `review_status`.

### Tab 4: `Exception Log` (Actionable Discrepancies)
*Update Strategy: Deterministic overwrite with active open exceptions*
- `timestamp_utc`, `sku`, `product_title`, `exception_type`, `severity`, `details`.

### Tab 5: `Sync Log` (Historical Execution Audit)
*Update Strategy: Row append (Distinct entry per sync run)*
- `run_id`, `started_at_utc`, `finished_at_utc`, `source`, `status`, `records_read`, `records_written`, `error_summary`, `trigger`.
