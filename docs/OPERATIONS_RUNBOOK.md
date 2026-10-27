# Helfen Operations Hub — Operations Runbook

## 1. System Summary

The Helfen Operations Hub automates inventory synchronization, reconciliation, and reorder planning between Shopify development stores, Google Sheets, and cross-channel exports (Amazon/ERP).

- **Shopify Connector:** Read-only Admin GraphQL API client using Client Credentials Grant.
- **Google Sheets Connector:** Service Account authenticated client performing deterministic snapshot replacements and audit append logs.
- **Service Layer:** In-memory normalization, sales velocity calculations, and exception tagging.
- **Dashboard UI:** Interactive Streamlit application supporting manual CSV mode, local demo data, and real-time API triggers.
- **CLI Runner:** Standalone executable script for headless automation (`scripts/sync_inventory.py`).

---

## 2. Operating Modes & Daily Workflows

### Mode A: Streamlit Dashboard UI
Launch the dashboard:
```bash
.venv/bin/streamlit run app.py
```

1. **Upload CSV (Default):** Upload exports directly in your browser. All processing stays local to your session.
2. **Demo Data:** Test calculations immediately using sample datasets in `sample_data/`.
3. **Shopify API:** Test connection and pull live location-level inventory directly from your Shopify store.
4. **Shopify + Google Sheets:**
   - Click **📊 Test Sheets Connection** to confirm connectivity and tab structure.
   - Click **🚀 Run Sync to Sheets** to fetch inventory, calculate recommendations, and publish updates to Google Sheets with real-time row counts and status indicators.

### Mode B: Command-Line Synchronization (CLI)
Run manual or scheduled syncs from the terminal:

```bash
# Full synchronization (Fetches Shopify -> Calculates -> Updates Google Sheets)
.venv/bin/python scripts/sync_inventory.py

# Preview / Dry-run (Fetches & computes recommendations without writing to Sheets)
.venv/bin/python scripts/sync_inventory.py --dry-run

# Verbose debug logging
.venv/bin/python scripts/sync_inventory.py --verbose
```

**Exit Codes:**
- `0`: Success or preview completed.
- `1`: Synchronization failed (error summary printed to stderr/stdout).

---

## 3. Google Sheets Tab Management

| Tab Name | Maintained By | Update Behavior | Description |
| :--- | :--- | :--- | :--- |
| **Supplier Master** | Operations Staff (Human) | **Read-Only** | Holds vendor MOQs, lead times, target stocks, and thresholds. **Never cleared or overwritten by code.** |
| **Inventory Snapshot** | Helfen Hub | Deterministic Overwrite | Current Shopify stock levels by SKU and location with timestamp. |
| **Reorder Recommendations** | Helfen Hub | Deterministic Overwrite | Actionable reorder quantities and urgency flags. |
| **Exception Log** | Helfen Hub | Deterministic Overwrite | Open catalog or supplier mapping anomalies requiring review. |
| **Sync Log** | Helfen Hub | Row Append | Permanent audit trail recording each run's timestamp, record counts, and status. |

---

## 4. Troubleshooting & Recovery Procedures

### Issue 1: `Shopify 401 Unauthorized`
- **Cause:** Client ID or Client Secret is incorrect or expired.
- **Action:**
  1. Check `.env` to verify `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`.
  2. Verify that the app is installed on your dev store in the same organization.

### Issue 2: `Shopify GraphQL Error: Access denied for field`
- **Cause:** Missing required access scope in Shopify Dev Dashboard.
- **Action:** Ensure `read_products` and `read_inventory` are checked in your app configuration.

### Issue 3: `Google Sheets HTTP 403 Forbidden`
- **Cause:** The spreadsheet has not been shared with the Service Account email.
- **Action:** Open your Google Sheet, click **Share**, and add the `client_email` found in `service_account.json` with **Editor** permissions.

### Issue 4: `Google Sheets HTTP 404 Not Found`
- **Cause:** `GOOGLE_SHEET_ID` in `.env` is invalid or contains extraneous URL parameters.
- **Action:** Copy only the string between `/d/` and `/edit` from your browser URL.

### Issue 5: Untracked or Missing SKU Warnings in Exception Log
- **Cause:** Product variants in Shopify do not have an SKU assigned or inventory tracking is disabled.
- **Action:** In Shopify Admin, open the flagged product, check "Track quantity", and provide an SKU.

---

## 5. Security & Secret Rotation

- **No Secrets in Git:** `.env` and `service_account.json` are strictly ignored by `.gitignore`.
- **Rotating Shopify Secrets:**
  1. In Shopify Dev Dashboard > App > API credentials, generate a new Client Secret.
  2. Update `SHOPIFY_CLIENT_SECRET` in `.env`.
  3. No application restart is needed (the client reads `.env` dynamically).
- **Rotating Google Service Keys:**
  1. In Google Cloud Console > Service Accounts, create a new JSON key.
  2. Overwrite `service_account.json` locally and delete the old key in Google Cloud.
