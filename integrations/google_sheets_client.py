"""
Google Sheets API Client.

Implements read/write interactions with Google Sheets via a Google Cloud Service Account.

Features:
- Deterministic range updates (replaces data ranges rather than infinitely appending).
- Distinct execution appending for audit sync logs.
- Strict tab boundary preservation (never overwrites Supplier Master or unmanaged ranges).
- Sanitized error handling with zero credential leakage.
"""

from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TAB_SUPPLIER_MASTER = "Supplier Master"
TAB_INVENTORY_SNAPSHOT = "Inventory Snapshot"
TAB_REORDER_RECOMMENDATIONS = "Reorder Recommendations"
TAB_EXCEPTION_LOG = "Exception Log"
TAB_SYNC_LOG = "Sync Log"

SUPPLIER_MASTER_COLUMNS = [
    "sku", "supplier_id", "supplier_name", "supplier_sku",
    "lead_time_days", "minimum_order_qty", "reorder_threshold",
    "target_stock", "notes", "updated_at"
]

INVENTORY_SNAPSHOT_COLUMNS = [
    "sync_timestamp_utc", "product_id", "product_title", "variant_id",
    "sku", "inventory_item_id", "location", "available_quantity",
    "inventory_tracked", "validation_status"
]

REORDER_RECOMMENDATIONS_COLUMNS = [
    "generated_at_utc", "sku", "product_title", "location",
    "available_quantity", "reorder_threshold", "target_stock",
    "supplier_name", "lead_time_days", "minimum_order_qty",
    "suggested_quantity", "reason", "review_status"
]

EXCEPTION_LOG_COLUMNS = [
    "timestamp_utc", "sku", "product_title", "exception_type",
    "severity", "details"
]

SYNC_LOG_COLUMNS = [
    "run_id", "started_at_utc", "finished_at_utc", "source",
    "status", "records_read", "records_written", "error_summary", "trigger"
]


class GoogleSheetsAPIError(Exception):
    """Base exception for Google Sheets API errors with sanitized messages."""
    pass


class GoogleSheetsClient:
    """Client for Google Sheets API v4 using a Service Account."""

    def __init__(
        self,
        credentials_file: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
        service_override: Optional[Any] = None,
    ):
        self.credentials_file = credentials_file if credentials_file is not None else os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        self.spreadsheet_id = spreadsheet_id if spreadsheet_id is not None else os.getenv("GOOGLE_SHEET_ID", "")
        self._service = service_override

    def is_configured(self) -> bool:
        """Checks if service account path and spreadsheet ID are configured and exist."""
        if not self.spreadsheet_id or self.spreadsheet_id == "your_spreadsheet_id_here":
            return False
        if self._service is not None:
            return True
        if not self.credentials_file:
            return False
        return os.path.exists(self.credentials_file)

    def get_missing_config_fields(self) -> List[str]:
        """Returns missing configuration fields for user guidance."""
        missing = []
        if not self.spreadsheet_id or self.spreadsheet_id == "your_spreadsheet_id_here":
            missing.append("GOOGLE_SHEET_ID")
        if not self.credentials_file:
            missing.append("GOOGLE_SERVICE_ACCOUNT_FILE")
        elif not os.path.exists(self.credentials_file) and self._service is None:
            missing.append(f"File not found: {self.credentials_file}")
        return missing

    def _get_service(self):
        """Initializes and caches the Google Sheets v4 service client."""
        if self._service is not None:
            return self._service

        if not self.is_configured():
            missing = ", ".join(self.get_missing_config_fields())
            raise GoogleSheetsAPIError(f"Google Sheets client is not configured. Missing: {missing}")

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                self.credentials_file, scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            return self._service
        except Exception as e:
            raise GoogleSheetsAPIError(f"Failed to authenticate with Google Sheets Service Account: {str(e)}")

    def test_connection(self) -> Dict[str, Any]:
        """
        Tests connection by querying the spreadsheet's metadata and inspecting its tabs.
        """
        service = self._get_service()
        try:
            sheet_meta = (
                service.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id)
                .execute()
            )
            title = sheet_meta.get("properties", {}).get("title", "Untitled")
            sheets = sheet_meta.get("sheets", [])
            tab_names = [s.get("properties", {}).get("title", "") for s in sheets]

            required_tabs = [
                TAB_SUPPLIER_MASTER,
                TAB_INVENTORY_SNAPSHOT,
                TAB_REORDER_RECOMMENDATIONS,
                TAB_EXCEPTION_LOG,
                TAB_SYNC_LOG,
            ]
            missing_tabs = [t for t in required_tabs if t not in tab_names]

            return {
                "success": True,
                "title": title,
                "spreadsheet_id": self.spreadsheet_id,
                "tabs_found": tab_names,
                "missing_tabs": missing_tabs,
            }
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg:
                raise GoogleSheetsAPIError(
                    f"Spreadsheet not found (HTTP 404). Check GOOGLE_SHEET_ID={self.spreadsheet_id}"
                )
            elif "403" in err_msg:
                raise GoogleSheetsAPIError(
                    "Permission denied (HTTP 403). Make sure the spreadsheet is shared with "
                    "your Service Account email as 'Editor'."
                )
            raise GoogleSheetsAPIError(f"Google Sheets test connection failed: {err_msg}")

    def read_supplier_master(self) -> pd.DataFrame:
        """
        Reads the 'Supplier Master' tab and returns a validated pandas DataFrame.
        """
        service = self._get_service()
        range_name = f"'{TAB_SUPPLIER_MASTER}'!A1:Z500"
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=range_name)
                .execute()
            )
        except Exception as e:
            raise GoogleSheetsAPIError(f"Could not read Supplier Master: {str(e)}")

        rows = result.get("values", [])
        if not rows:
            return pd.DataFrame(columns=SUPPLIER_MASTER_COLUMNS)

        # Normalize rows in case user pasted tab, pipe, or comma separated values into column A
        normalized_rows = []
        for r in rows:
            if len(r) == 1 and isinstance(r[0], str):
                val = r[0]
                if "\t" in val:
                    normalized_rows.append([x.strip() for x in val.split("\t")])
                elif "|" in val:
                    normalized_rows.append([x.strip() for x in val.split("|")])
                elif "," in val:
                    normalized_rows.append([x.strip() for x in val.split(",")])
                else:
                    normalized_rows.append(r)
            else:
                normalized_rows.append(r)

        header = [
            str(c).strip().lower().replace(" ", "_").replace("-", "_")
            for c in normalized_rows[0]
        ]
        data = normalized_rows[1:]
        if not data:
            return pd.DataFrame(columns=header)

        # Pad rows to match header length
        padded_data = [row + [""] * (len(header) - len(row)) for row in data]
        df = pd.DataFrame(padded_data, columns=header)
        return df

    def write_inventory_snapshot(self, df: pd.DataFrame) -> int:
        """
        Deterministically replaces the complete 'Inventory Snapshot' tab with current Shopify data.
        Clears the entire tab grid (range="'Inventory Snapshot'") before writing to prevent stale rows.

        Returns:
            int: The count of data rows written (strictly excluding the header row).
        """
        service = self._get_service()
        # Ensure column structure
        cols = INVENTORY_SNAPSHOT_COLUMNS
        if df is not None and not df.empty:
            out_df = pd.DataFrame(index=df.index)
            for c in cols:
                if c in df.columns:
                    out_df[c] = df[c]
                elif c == "sync_timestamp_utc":
                    out_df[c] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                elif c == "location":
                    loc_c = "location_name" if "location_name" in df.columns else "location"
                    out_df[c] = df[loc_c] if loc_c in df.columns else "Default"
                else:
                    out_df[c] = ""
            out_df = out_df[cols].fillna("")
        else:
            out_df = pd.DataFrame(columns=cols)

        values = [cols] + out_df.values.tolist()

        try:
            # Clear complete tab first so no stale or leftover rows remain
            service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_INVENTORY_SNAPSHOT}'",
            ).execute()

            # Write header and updated rows
            service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_INVENTORY_SNAPSHOT}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            return len(out_df)
        except Exception as e:
            raise GoogleSheetsAPIError(f"Failed to write Inventory Snapshot: {str(e)}")

    def write_reorder_recommendations(self, df: pd.DataFrame) -> int:
        """
        Deterministically replaces the complete 'Reorder Recommendations' tab.
        Clears the entire tab grid (range="'Reorder Recommendations'") before writing to prevent stale rows.

        Returns:
            int: The count of data rows written (strictly excluding the header row).
        """
        service = self._get_service()
        cols = REORDER_RECOMMENDATIONS_COLUMNS
        if df is not None and not df.empty:
            out_df = pd.DataFrame(index=df.index)
            for c in cols:
                if c in df.columns:
                    out_df[c] = df[c]
                else:
                    out_df[c] = ""
            out_df = out_df[cols].fillna("")
        else:
            out_df = pd.DataFrame(columns=cols)

        values = [cols] + out_df.values.tolist()

        try:
            # Clear complete tab first so no stale or leftover rows remain
            service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_REORDER_RECOMMENDATIONS}'",
            ).execute()

            service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_REORDER_RECOMMENDATIONS}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            return len(out_df)
        except Exception as e:
            raise GoogleSheetsAPIError(f"Failed to write Reorder Recommendations: {str(e)}")

    def write_exception_log(self, df: pd.DataFrame) -> int:
        """
        Deterministically replaces the complete 'Exception Log' tab with current actionable exceptions.
        Clears the entire tab grid (range="'Exception Log'") before writing to prevent stale rows.

        Returns:
            int: The count of exception data rows written (strictly excluding the header row).
        """
        service = self._get_service()
        cols = EXCEPTION_LOG_COLUMNS
        if df is not None and not df.empty:
            out_df = pd.DataFrame(index=df.index)
            for c in cols:
                if c in df.columns:
                    out_df[c] = df[c]
                else:
                    out_df[c] = ""
            out_df = out_df[cols].fillna("")
        else:
            out_df = pd.DataFrame(columns=cols)

        values = [cols] + out_df.values.tolist()

        try:
            # Clear complete tab first so no stale or leftover rows remain
            service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_EXCEPTION_LOG}'",
            ).execute()

            service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_EXCEPTION_LOG}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            return len(out_df)
        except Exception as e:
            raise GoogleSheetsAPIError(f"Failed to write Exception Log: {str(e)}")

    def append_sync_log(self, entry: Dict[str, Any]) -> None:
        """
        Appends an execution log record to the 'Sync Log' tab.
        Ensures headers exist if table is empty.
        """
        service = self._get_service()
        row = [
            str(entry.get("run_id", "")),
            str(entry.get("started_at_utc", "")),
            str(entry.get("finished_at_utc", "")),
            str(entry.get("source", "")),
            str(entry.get("status", "")),
            int(entry.get("records_read", 0)),
            int(entry.get("records_written", 0)),
            str(entry.get("error_summary", "")),
            str(entry.get("trigger", "manual")),
        ]

        try:
            # Check if header exists
            check_header = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=f"'{TAB_SYNC_LOG}'!A1:I1")
                .execute()
            )
            header_vals = check_header.get("values", [])
            if not header_vals:
                service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{TAB_SYNC_LOG}'!A1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [SYNC_LOG_COLUMNS]},
                ).execute()

            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{TAB_SYNC_LOG}'!A:I",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
        except Exception as e:
            raise GoogleSheetsAPIError(f"Failed to append to Sync Log: {str(e)}")
