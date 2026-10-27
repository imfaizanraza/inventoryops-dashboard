# 🎙️ Job Interview Demo Script: InventoryOps Dashboard

**Demonstration Duration:** 3–5 minutes
**Target Audience:** Technical Interviewer / Engineering Manager / Solutions Architect
**Objective:** Present the business problem, architectural design, data-quality safeguards, testing rigor, and live operational workflows of the project.

---

## ⏱️ Minute-by-Minute Walkthrough

### Minute 1: The Hook & Architectural Overview (0:00 – 1:00)

> *"Hi everyone. Today I'm demonstrating **InventoryOps Dashboard**, an e-commerce operations hub and synchronization pipeline I built in Python.*
>
> *In multi-channel retail, operations teams constantly struggle with three problems: inventory desynchronization between Shopify and Amazon, stale spreadsheet syncs that leave ghost rows behind when catalogs shrink, and silent errors when products missing SKUs enter purchasing workflows.*
>
> *I designed this platform to bridge Shopify's live catalog with human-maintained purchasing rules in Google Sheets. It features a read-only Shopify GraphQL Admin API connector, a Service Account Google Sheets integration with full-grid deterministic updates, an automated reorder calculation engine, and an interactive Streamlit operations dashboard."*

**Visual Action:** Show the [Architecture Diagram](file:///Users/faizan/Projects/inventoryops_dashboard/docs/ARCHITECTURE.md#1-system-overview--end-to-end-data-flow) or the terminal open to the project root.

---

### Minute 2: Safety & Multi-Channel Reconciliation (1:00 – 2:00)

> *"Let’s look at the operational dashboard.*
>
> *One foundational design principle is **strict read-only safety**. The platform requests only `read_products` and `read_inventory` scopes. It never mutates store inventory or creates purchase orders; reorder recommendations are generated for human review.*
>
> *In the UI, users can switch between live API mode, offline CSV mode, or sample demo data.*
>
> *Under **Stock Comparison**, the system performs an outer-join reconciliation between Shopify and Amazon exports—flagging stock mismatches, missing items on either channel, and items breaching low-stock thresholds.*
>
> *Under **Supplier Reordering**, the engine calculates daily velocity, target stock cover, and supplier lead times, automatically applying the vendor's Minimum Order Quantity (MOQ) floor."*

**Visual Action:**
1. Navigate to Streamlit UI in browser (`http://localhost:8501`).
2. Show **Tab 1: Stock comparison** (demonstrate Match vs. Mismatch metrics).
3. Show **Tab 3: Supplier reordering** (show Healthy vs. Reorder Review Needed vs. Urgent Reorder cards and MOQ adjustments).

---

### Minute 3: Data Quality & Stale-Row Prevention (2:00 – 3:00)

> *"A critical challenge I solved in this project was **data-quality drift and stale rows in Google Sheets**.*
>
> *Standard spreadsheet automations often append records or overwrite bounded ranges like `A1:Z50`. If Run 1 produces 50 recommendations and Run 2 produces only 5, 45 stale ghost rows remain behind.*
>
> *To fix this deterministically, my Google Sheets connector performs an unbounded full-grid clear (`values().clear(range=f"'{tab}'")`) immediately before writing updated data. If a tab has 0 exceptions, it clears the sheet and writes only the header row, leaving zero stale rows.*
>
> *Furthermore, I implemented strict **Missing-SKU Exception Isolation**: products created in Shopify without an SKU cannot be ordered from suppliers. The engine excludes them from the Reorder Recommendations tab and isolates them into the `Exception Log` under `CRITICAL: Missing SKU`. Valid SKUs without a supplier entry remain in recommendations as `Unassigned` and are flagged as `WARNING: Missing Supplier Mapping`."*

**Visual Action:**
- Point to the Google Sheets tabs:
  - `Supplier Master`: Show that human-maintained parameters are strictly read-only and preserved.
  - `Reorder Recommendations`: Clean table containing only valid, actionable SKUs.
  - `Exception Log`: Clearly isolated actionable issues without duplicate noise.

---

### Minute 4: Headless CLI Automation & Auditability (3:00 – 4:00)

> *"In addition to the interactive Streamlit UI, the sync engine can be triggered headlessly for scheduled cron automation using `scripts/sync_inventory.py`.*
>
> *It supports dry-run previews and verbose logging."*

**Visual Action (Run in Terminal):**
```bash
.venv/bin/python scripts/sync_inventory.py --dry-run
```

> *"Notice the terminal output: it generates a unique run ID, records read, records written, and status. Every single sync writes an execution record to the append-only `Sync Log` tab in Google Sheets, creating a permanent audit trail for operations teams."*

---

### Minute 5: Engineering Rigor & Testing Strategy (4:00 – 5:00)

> *"Finally, I prioritized testing rigor. The test suite contains **39 unit tests** that execute completely offline in under one second.*
>
> *It tests GraphQL cursor pagination, token caching, Service Account authentication, dataset shrinkage, MOQ edge cases, missing-SKU exclusion, and Streamlit UI rendering without making any live network calls or exposing secrets."*

**Visual Action (Run in Terminal):**
```bash
.venv/bin/python -m unittest discover tests
```

> *"All 39 tests pass. In summary, InventoryOps demonstrates secure API integration, deterministic data engineering, fault-tolerant exception handling, and practical e-commerce operations automation. I'm happy to dive deeper into any part of the code or architecture."*

---

## 💡 Anticipated Technical Interview Questions & Answers

| Question | Strong Answer |
| :--- | :--- |
| **Why use Client Credentials Grant instead of standard OAuth redirect?** | *"Client Credentials Grant is the modern Shopify standard for private custom apps created in the Partner Dev Dashboard. Because this is a backend-to-backend automation without end-customer user sessions, it eliminates the need for interactive OAuth redirect loops while maintaining scoped access tokens."* |
| **How does your Google Sheets update prevent race conditions?** | *"In the current design, syncs are orchestrated sequentially by `SyncService`. In a multi-worker production deployment, I would add a distributed lock (e.g. Redis redlock) around the sync runner to guarantee single-writer concurrency across workers."* |
| **Why not automatically create Purchase Orders?** | *"In real-world e-commerce operations, automatically generating POs or pushing inventory changes without human review risks over-ordering and inventory drift. Keeping the ingestion read-only and presenting suggested quantities for buyer approval maintains essential human-in-the-loop validation."* |
| **How did you prevent credentials from leaking?** | *"All credentials reside in git-ignored `.env` and `service_account.json` files. Both API clients sanitize error responses, catching low-level HTTP exceptions and re-raising sanitized domain exceptions (`ShopifyAPIError`, `GoogleSheetsAPIError`) that never expose tokens or private keys."* |
