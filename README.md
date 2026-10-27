# 📦 InventoryOps Dashboard (Helfen Operations Hub)

A production-grade e-commerce operations hub and inventory synchronization pipeline connecting **Shopify GraphQL Admin API**, **Google Sheets API v4**, and multi-channel CSV exports with an interactive **Streamlit** dashboard.

---

## 🎯 Business Problem

E-commerce operations teams face recurring operational risks across multi-channel environments:
- **Inventory Desynchronization:** Stock discrepancies between Shopify, Amazon, and ERP systems result in overselling, stockouts, and cancellation penalties.
- **Data Quality & Catalog Drift:** Products created without SKUs or unmapped to suppliers trigger silent purchasing errors or incorrect replenishment.
- **Spreadsheet Degradation & Stale Rows:** Traditional spreadsheet syncs append data or perform bounded overwrites, leaving ghost rows when catalogs or exception counts shrink.
- **Safety in Automation:** Unvetted scripts can unintentionally mutate store inventory or place unauthorized purchase orders.

**InventoryOps Dashboard** solves these challenges by combining read-only API connectors, deterministic sheet replacement, human-in-the-loop reorder suggestions, explicit exception isolation, and persistent audit logging.

---

## ✨ Features

- **Multi-Mode Operations Hub:**
  - **Shopify + Google Sheets (Live API):** End-to-end automated synchronization.
  - **Shopify API (Catalog Ingestion):** Read-only live catalog inspection.
  - **Upload CSV:** Offline operations auditing for Shopify, Amazon, order fulfillment, and sales history.
  - **Demo Data:** Pre-loaded mock datasets for testing and demonstration.
- **Cross-Channel Inventory Reconciliation:**
  - Compares Shopify vs. Amazon inventory on SKU.
  - Identifies stock mismatches, missing items on either channel, and low-stock thresholds.
- **Fulfillment & Order Exception Audit:**
  - Audits order tracking numbers, separating unfulfilled orders from fulfillment tracking anomalies.
  - Flags duplicate order IDs and multi-item shipments.
- **Supplier-Aware Reorder Engine:**
  - Integrates supplier lead times, minimum order quantities (MOQ), reorder thresholds, and target stock levels.
  - Generates clear action statuses: `Healthy`, `Reorder Review Needed`, and `Urgent Reorder Required`.
  - **Read-Only Safety:** Explicitly presents reorder quantities as purchasing recommendations without mutating store inventory or placing purchase orders.
- **Strict Data-Quality & Exception Isolation:**
  - Products without SKUs are routed exclusively to `Exception Log` (`Missing SKU`), preventing spurious supplier mappings and keeping `Reorder Recommendations` clean.
  - Valid SKUs lacking supplier assignments are flagged as `Missing Supplier Mapping` and categorized as `Unassigned`.
- **Deterministic Google Sheets Synchronization:**
  - Replaces `Inventory Snapshot`, `Reorder Recommendations`, and `Exception Log` via full-grid wiping (`values().clear`) before writing fresh results, eliminating leftover stale rows.
  - Preserves human-maintained `Supplier Master` without modification.
  - Appends every sync run to `Sync Log` for a permanent audit trail.
- **Headless CLI Runner:**
  - `scripts/sync_inventory.py` supports automated execution, `--dry-run`, `--no-sheets`, and `--verbose` logging for headless servers or cron jobs.

---

## 🛠️ Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Language** | Python 3.10+ (Tested on Python 3.12) |
| **User Interface** | Streamlit |
| **Data Processing** | pandas, NumPy |
| **E-Commerce API** | Shopify GraphQL Admin API (`2026-10`), Client Credentials Grant |
| **Cloud Storage** | Google Sheets API v4 (`google-api-python-client`, `google-auth`) |
| **Configuration** | python-dotenv |
| **Testing** | Python `unittest`, `unittest.mock` |

---

## 🚀 Setup Instructions

### 1. Clone & Environment Setup

```bash
# Clone the repository
git clone <repository-url>
cd inventoryops_dashboard

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the provided [.env.example](file:///Users/faizan/Projects/inventoryops_dashboard/.env.example) to `.env`:

```bash
cp .env.example .env
```

Configure your credentials in `.env` (never commit this file):

| Variable Name | Description |
| :--- | :--- |
| `SHOPIFY_STORE_DOMAIN` | Shopify development store myshopify domain (e.g. `your-store.myshopify.com`) |
| `SHOPIFY_API_VERSION` | Shopify Admin API version (e.g. `2026-10`) |
| `SHOPIFY_CLIENT_ID` | Dev Dashboard App Client ID |
| `SHOPIFY_CLIENT_SECRET` | Dev Dashboard App Client Secret |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to Google Cloud Service Account JSON key (e.g. `service_account.json`) |
| `GOOGLE_SHEET_ID` | Google Spreadsheet ID (from spreadsheet URL) |

> [!NOTE]
> Detailed walkthroughs for provisioning Shopify Dev Apps and Google Cloud Service Accounts are available in [docs/SETUP.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/SETUP.md) and [docs/OPERATIONS_RUNBOOK.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/OPERATIONS_RUNBOOK.md).

---

## 💻 Running the Application

### 1. Interactive Streamlit Dashboard

```bash
.venv/bin/streamlit run app.py
```
Open your browser at `http://localhost:8501`.

### 2. Headless CLI Synchronization

```bash
# Run full sync to Google Sheets
.venv/bin/python scripts/sync_inventory.py

# Run preview / dry-run without writing to Google Sheets
.venv/bin/python scripts/sync_inventory.py --dry-run

# Run with verbose debug logging
.venv/bin/python scripts/sync_inventory.py --verbose
```

---

## 🧪 Testing

The test suite runs completely offline using mocks, requiring zero live network calls or credentials:

```bash
.venv/bin/python -m unittest discover tests
```

Current test suite contains **39 unit tests** covering:
- GraphQL query construction and cursor pagination
- Service Account authorization and deterministic sheet replacement
- Dataset shrinkage and empty exception wiping
- Sales velocity, MOQ floor, and safety stock calculations
- Missing SKU exception routing and isolation
- Streamlit UI rendering and tab components

---

## ⚠️ Known Limitations

1. **Single-Store Scope:** Configured for single-tenant Shopify dev store connections. Multi-tenant retail networks require extending client config to support dynamic store tenancy.
2. **Channel Integration Scope:** Shopify is integrated via live GraphQL Admin API; Amazon and ERP reconciliation currently operate on CSV exports rather than live Amazon SP-API OAuth.
3. **Purchasing Boundary:** Suggested reorder quantities are intentionally presented as operational review recommendations. The system does not transmit purchase orders directly to vendor EDI/APIs to maintain strict human-in-the-loop validation.
4. **Task Scheduling:** Background syncs run via external schedulers (cron, GitHub Actions, or systemd timers) calling `scripts/sync_inventory.py` rather than an internal daemon thread.

---

## 📖 Documentation Index

- [docs/ARCHITECTURE.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/ARCHITECTURE.md): End-to-end data flow, authentication, pagination, deterministic updating, and testing design.
- [docs/DEMO_SCRIPT.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/DEMO_SCRIPT.md): 3–5 minute walkthrough guide for technical interviews.
- [docs/OPERATIONS_RUNBOOK.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/OPERATIONS_RUNBOOK.md): Operational procedures, tab management, troubleshooting, and secret rotation.
- [docs/DATA_DICTIONARY.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/DATA_DICTIONARY.md): Column definitions, schemas, and alias recognition.
- [docs/SETUP.md](file:///Users/faizan/Projects/inventoryops_dashboard/docs/SETUP.md): Step-by-step setup for Shopify Dev Dashboard and Google Cloud credentials.
