"""
Synchronization Service.

Orchestrates read-only ingestion from Shopify GraphQL Admin API,
merges with Google Sheets supplier master parameters, computes
reorder recommendations and exceptions, and updates Google Sheets.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

import pandas as pd

from integrations.google_sheets_client import GoogleSheetsClient, GoogleSheetsAPIError
from integrations.shopify_client import ShopifyClient, ShopifyAPIError
from services.reorder_service import calculate_reorder_recommendations

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates end-to-end data synchronization and audit logging."""

    def __init__(
        self,
        shopify_client: Optional[ShopifyClient] = None,
        sheets_client: Optional[GoogleSheetsClient] = None,
    ):
        self.shopify = shopify_client or ShopifyClient()
        self.sheets = sheets_client or GoogleSheetsClient()

    def run_sync(
        self,
        publish_to_sheets: bool = True,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """
        Executes end-to-end synchronization.

        Steps:
        1. Fetch location-level inventory from Shopify Admin GraphQL API.
        2. Read supplier rules from Google Sheets (or fallback if empty).
        3. Calculate reorder recommendations and operational exceptions.
        4. (If publish_to_sheets=True) Update Google Sheets snapshot, recommendations, exceptions, and append sync log.
        """
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        records_read = 0
        records_written = 0
        error_summary = ""
        status = "FAILED"
        inventory_df = pd.DataFrame()
        recommendations_df = pd.DataFrame()
        exceptions_df = pd.DataFrame()

        try:
            # 1. Fetch Shopify Inventory
            logger.info("Fetching Shopify inventory...")
            inventory_df = self.shopify.fetch_inventory()
            records_read = len(inventory_df)

            # 2. Fetch Supplier Master from Google Sheets (if configured)
            supplier_df = None
            if self.sheets.is_configured():
                try:
                    supplier_df = self.sheets.read_supplier_master()
                except Exception as e:
                    logger.warning(f"Could not read Supplier Master from Sheets: {e}")
                    error_summary = f"Supplier Master read warning: {str(e)[:100]}"

            # 3. Calculate Reorder Recommendations and Exceptions
            recommendations_df, exceptions_df = calculate_reorder_recommendations(
                inventory_df=inventory_df,
                supplier_df=supplier_df,
            )

            # 4. Publish to Google Sheets if requested
            # Exact Write Order:
            #   1. write_inventory_snapshot (replaces complete 'Inventory Snapshot' tab)
            #   2. write_reorder_recommendations (replaces complete 'Reorder Recommendations' tab)
            #   3. write_exception_log (replaces complete 'Exception Log' tab)
            #   4. append_sync_log (appends 1 row to 'Sync Log' tab; never clears history)
            # Safety:
            #   - 'Supplier Master' is NEVER written to or cleared (read-only parameter source).
            #   - 'Sync Log' is append-only audit trail and is never overwritten.
            # Metric Definition:
            #   - records_written is strictly defined as the sum of data rows written across
            #     the three managed output tabs (Inventory Snapshot + Reorder Recommendations +
            #     Exception Log), EXCLUDING header rows. It does not count the Sync Log row itself.
            if publish_to_sheets and self.sheets.is_configured():
                written_snapshot = self.sheets.write_inventory_snapshot(inventory_df)
                written_reorders = self.sheets.write_reorder_recommendations(recommendations_df)
                written_exceptions = self.sheets.write_exception_log(exceptions_df)
                records_written = written_snapshot + written_reorders + written_exceptions
                status = "SUCCESS"
            elif not publish_to_sheets:
                status = "PREVIEW_OK"
            else:
                status = "LOCAL_ONLY"
                error_summary = "Google Sheets not configured; results kept in local memory only."

        except ShopifyAPIError as e:
            status = "FAILED"
            error_summary = f"Shopify Error: {str(e)}"
            logger.error(error_summary)
        except GoogleSheetsAPIError as e:
            status = "FAILED"
            error_summary = f"Google Sheets Error: {str(e)}"
            logger.error(error_summary)
        except Exception as e:
            status = "FAILED"
            error_summary = f"Unexpected Error: {e.__class__.__name__}: {str(e)}"
            logger.error(error_summary, exc_info=True)

        finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        sync_result = {
            "run_id": run_id,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "source": f"Shopify GraphQL ({self.shopify.store_domain or 'not-configured'})",
            "status": status,
            "records_read": records_read,
            "records_written": records_written,
            "error_summary": error_summary,
            "trigger": trigger,
            "inventory_df": inventory_df,
            "recommendations_df": recommendations_df,
            "exceptions_df": exceptions_df,
        }

        # Append to Google Sheets sync log if Sheets is available
        if self.sheets.is_configured():
            try:
                self.sheets.append_sync_log(sync_result)
            except Exception as e:
                logger.error(f"Failed to append to Sync Log in Google Sheets: {e}")

        return sync_result
