"""
Tests for Google Sheets API Client.
Uses mocked Google Sheets v4 discovery service to verify reads, writes, and error handling offline.
"""

import unittest
from unittest.mock import MagicMock
import pandas as pd

from integrations.google_sheets_client import (
    GoogleSheetsClient,
    GoogleSheetsAPIError,
    TAB_SUPPLIER_MASTER,
    TAB_INVENTORY_SNAPSHOT,
    TAB_REORDER_RECOMMENDATIONS,
    TAB_EXCEPTION_LOG,
    TAB_SYNC_LOG,
)


class TestGoogleSheetsClient(unittest.TestCase):

    def setUp(self):
        self.mock_service = MagicMock()
        self.client = GoogleSheetsClient(
            credentials_file="dummy_service_account.json",
            spreadsheet_id="dummy_sheet_id_12345",
            service_override=self.mock_service,
        )

    def test_configuration_detection(self):
        self.assertTrue(self.client.is_configured())

        empty_client = GoogleSheetsClient(credentials_file="", spreadsheet_id="")
        self.assertFalse(empty_client.is_configured())
        self.assertIn("GOOGLE_SHEET_ID", empty_client.get_missing_config_fields())

    def test_test_connection_success(self):
        # Mock spreadsheets().get()
        mock_get = MagicMock()
        mock_get.execute.return_value = {
            "properties": {"title": "Helfen Operations Data Hub"},
            "sheets": [
                {"properties": {"title": TAB_SUPPLIER_MASTER}},
                {"properties": {"title": TAB_INVENTORY_SNAPSHOT}},
                {"properties": {"title": TAB_REORDER_RECOMMENDATIONS}},
                {"properties": {"title": TAB_EXCEPTION_LOG}},
                {"properties": {"title": TAB_SYNC_LOG}},
            ],
        }
        self.mock_service.spreadsheets.return_value.get.return_value = mock_get

        res = self.client.test_connection()
        self.assertTrue(res["success"])
        self.assertEqual(res["title"], "Helfen Operations Data Hub")
        self.assertEqual(len(res["missing_tabs"]), 0)

    def test_read_supplier_master(self):
        # Mock spreadsheets().values().get()
        mock_values_get = MagicMock()
        mock_values_get.execute.return_value = {
            "values": [
                ["sku", "supplier_name", "lead_time_days", "minimum_order_qty", "reorder_threshold"],
                ["WATCH-001", "Strap Supply Co", "10", "50", "20"],
                ["WATCH-002", "Leather Goods Ltd", "21", "25", "15"],
            ]
        }
        self.mock_service.spreadsheets.return_value.values.return_value.get.return_value = mock_values_get

        df = self.client.read_supplier_master()
        self.assertEqual(len(df), 2)
        self.assertIn("sku", df.columns)
        self.assertEqual(df["sku"].iloc[0], "WATCH-001")
        self.assertEqual(df["supplier_name"].iloc[0], "Strap Supply Co")

    def test_write_inventory_snapshot_deterministic(self):
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        test_df = pd.DataFrame([
            {
                "sync_timestamp_utc": "2026-10-27 12:00:00 UTC",
                "product_id": "gid://shopify/Product/1",
                "product_title": "Minimal Watch",
                "variant_id": "gid://shopify/ProductVariant/101",
                "sku": "WATCH-001",
                "inventory_item_id": "gid://shopify/InventoryItem/501",
                "location": "Warehouse A",
                "available_quantity": 42,
                "inventory_tracked": True,
                "validation_status": "OK",
            }
        ])

        rows_written = self.client.write_inventory_snapshot(test_df)
        self.assertEqual(rows_written, 1)

        # Confirm clear was called with complete tab range before update
        values_mock.clear.assert_called_with(
            spreadsheetId="dummy_sheet_id_12345",
            range=f"'{TAB_INVENTORY_SNAPSHOT}'",
        )
        values_mock.update.assert_called_once()

    def test_write_reorder_recommendations_clears_complete_tab(self):
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        test_reorders = pd.DataFrame([
            {
                "generated_at_utc": "2026-10-28 00:00:00 UTC",
                "sku": "SKU-TEST",
                "product_title": "Test Snowboard",
                "location": "Warehouse",
                "available_quantity": 5,
                "reorder_threshold": 10,
                "target_stock": 50,
                "supplier_name": "Test Supplier",
                "lead_time_days": 14,
                "minimum_order_qty": 20,
                "suggested_quantity": 45,
                "reason": "Stock below threshold",
                "review_status": "Reorder Review Needed",
            }
        ])

        rows = self.client.write_reorder_recommendations(test_reorders)
        self.assertEqual(rows, 1)

        # Confirm clear was invoked with the full tab range
        values_mock.clear.assert_called_with(
            spreadsheetId="dummy_sheet_id_12345",
            range=f"'{TAB_REORDER_RECOMMENDATIONS}'",
        )
        values_mock.update.assert_called_once()

    def test_write_exception_log_clears_complete_tab(self):
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        test_exceptions = pd.DataFrame([
            {
                "timestamp_utc": "2026-10-28 00:00:00 UTC",
                "sku": "MISSING_SKU",
                "product_title": "Item Without SKU",
                "exception_type": "Missing SKU",
                "severity": "CRITICAL",
                "details": "Variant has no SKU identifier.",
            }
        ])

        rows = self.client.write_exception_log(test_exceptions)
        self.assertEqual(rows, 1)

        values_mock.clear.assert_called_with(
            spreadsheetId="dummy_sheet_id_12345",
            range=f"'{TAB_EXCEPTION_LOG}'",
        )
        values_mock.update.assert_called_once()

    def test_second_sync_fewer_rows_removes_stale_records(self):
        """
        Regression test: When a previous run had 5 rows and a subsequent run has only 2 rows,
        the full tab clear ensures the 3 stale rows are completely wiped out.
        """
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        # Run 1: 5 recommendations
        df_run1 = pd.DataFrame([
            {"sku": f"SKU-{i}", "suggested_quantity": 50, "review_status": "Reorder Review Needed"}
            for i in range(5)
        ])
        count1 = self.client.write_reorder_recommendations(df_run1)
        self.assertEqual(count1, 5)

        # Run 2: Only 2 recommendations (dataset shrink)
        df_run2 = pd.DataFrame([
            {"sku": f"SKU-{i}", "suggested_quantity": 25, "review_status": "Healthy"}
            for i in range(2)
        ])
        count2 = self.client.write_reorder_recommendations(df_run2)
        self.assertEqual(count2, 2)

        # Verify clear was executed on both calls with full sheet range
        self.assertEqual(values_mock.clear.call_count, 2)
        values_mock.clear.assert_called_with(
            spreadsheetId="dummy_sheet_id_12345",
            range=f"'{TAB_REORDER_RECOMMENDATIONS}'",
        )

    def test_supplier_master_preservation_across_all_writes(self):
        """
        Ensures Supplier Master is never targeted by any clear or update call.
        """
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        dummy_df = pd.DataFrame([{"sku": "SKU-1", "available_quantity": 10}])

        self.client.write_inventory_snapshot(dummy_df)
        self.client.write_reorder_recommendations(dummy_df)
        self.client.write_exception_log(dummy_df)

        # Check every single clear call
        for call_args in values_mock.clear.call_args_list:
            target_range = call_args[1].get("range", "")
            self.assertNotIn(TAB_SUPPLIER_MASTER, target_range, "Supplier Master was targeted by clear!")

        # Check every single update call
        for call_args in values_mock.update.call_args_list:
            target_range = call_args[1].get("range", "")
            self.assertNotIn(TAB_SUPPLIER_MASTER, target_range, "Supplier Master was targeted by update!")

    def test_write_exception_log_empty_clears_and_writes_header_only(self):
        """
        Regression test: When exceptions DataFrame is empty (0 exceptions),
        the sheet is cleared and only the header row is written. Returns 0 records written.
        """
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        empty_df = pd.DataFrame()
        count = self.client.write_exception_log(empty_df)

        self.assertEqual(count, 0)
        values_mock.clear.assert_called_with(
            spreadsheetId="dummy_sheet_id_12345",
            range=f"'{TAB_EXCEPTION_LOG}'",
        )
        values_mock.update.assert_called_once()
        call_args = values_mock.update.call_args[1]
        self.assertEqual(call_args["range"], f"'{TAB_EXCEPTION_LOG}'!A1")
        # Ensure only 1 row (the header) was written
        self.assertEqual(len(call_args["body"]["values"]), 1)
        self.assertEqual(call_args["body"]["values"][0], [
            "timestamp_utc", "sku", "product_title", "exception_type",
            "severity", "details"
        ])

    def test_dataset_shrinkage_across_all_tabs(self):
        """
        Regression test: Verifies that when any output tab shrinks from a larger dataset
        to a smaller dataset (or zero), the tab is completely cleared first, leaving no stale rows.
        """
        mock_clear = MagicMock()
        mock_clear.execute.return_value = {}
        mock_update = MagicMock()
        mock_update.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.clear.return_value = mock_clear
        values_mock.update.return_value = mock_update

        # Run 1: 10 inventory items, 8 recommendations, 5 exceptions
        inv_large = pd.DataFrame([{"sku": f"SKU-{i}", "available_quantity": 20} for i in range(10)])
        rec_large = pd.DataFrame([{"sku": f"SKU-{i}", "suggested_quantity": 10, "review_status": "Reorder Review Needed"} for i in range(8)])
        exc_large = pd.DataFrame([{"sku": f"SKU-{i}", "exception_type": "Missing Supplier Mapping"} for i in range(5)])

        r1_inv = self.client.write_inventory_snapshot(inv_large)
        r1_rec = self.client.write_reorder_recommendations(rec_large)
        r1_exc = self.client.write_exception_log(exc_large)
        self.assertEqual(r1_inv + r1_rec + r1_exc, 23)

        # Run 2: 3 inventory items, 1 recommendation, 0 exceptions
        inv_small = pd.DataFrame([{"sku": f"SKU-{i}", "available_quantity": 50} for i in range(3)])
        rec_small = pd.DataFrame([{"sku": "SKU-0", "suggested_quantity": 5, "review_status": "Healthy"}])
        exc_empty = pd.DataFrame()

        r2_inv = self.client.write_inventory_snapshot(inv_small)
        r2_rec = self.client.write_reorder_recommendations(rec_small)
        r2_exc = self.client.write_exception_log(exc_empty)

        self.assertEqual(r2_inv, 3)
        self.assertEqual(r2_rec, 1)
        self.assertEqual(r2_exc, 0)
        self.assertEqual(r2_inv + r2_rec + r2_exc, 4)

        # Verify clear was called 6 times total (3 tabs x 2 runs)
        self.assertEqual(values_mock.clear.call_count, 6)

    def test_sync_log_is_strictly_append_only(self):
        """
        Regression test: Sync Log must NEVER be cleared. It is append-only audit trail.
        """
        mock_get_header = MagicMock()
        mock_get_header.execute.return_value = {"values": [["run_id", "started_at_utc"]]}
        mock_append = MagicMock()
        mock_append.execute.return_value = {}

        values_mock = self.mock_service.spreadsheets.return_value.values.return_value
        values_mock.get.return_value = mock_get_header
        values_mock.append.return_value = mock_append

        entry1 = {"run_id": "run_001", "records_read": 28, "records_written": 32, "status": "SUCCESS"}
        entry2 = {"run_id": "run_002", "records_read": 28, "records_written": 32, "status": "SUCCESS"}

        self.client.append_sync_log(entry1)
        self.client.append_sync_log(entry2)

        # Verify clear was NEVER called on Sync Log
        for call_args in values_mock.clear.call_args_list:
            target_range = call_args[1].get("range", "")
            self.assertNotIn(TAB_SYNC_LOG, target_range, "Sync Log was targeted by clear!")

        # Verify append was called twice
        self.assertEqual(values_mock.append.call_count, 2)


if __name__ == "__main__":
    unittest.main()
