"""
Tests for Domain Services (Inventory, Reorder, and Sync).
"""

import unittest
from unittest.mock import MagicMock
import numpy as np
import pandas as pd

from services.inventory_service import (
    clean_sku,
    to_num,
    inventory_normalize,
    reconcile_inventory,
    normalize_shopify_graphql_products,
)
from services.reorder_service import calculate_reorder_recommendations
from services.sync_service import SyncService


class TestInventoryService(unittest.TestCase):

    def test_clean_sku(self):
        s = pd.Series(["  watch-001  ", "1002.0", "WATCH\t003\n", "nan", None, ""])
        cleaned = clean_sku(s)
        self.assertEqual(cleaned[0], "WATCH-001")
        self.assertEqual(cleaned[1], "1002")
        self.assertEqual(cleaned[2], "WATCH003")
        self.assertTrue(pd.isna(cleaned[3]))
        self.assertTrue(pd.isna(cleaned[4]))
        self.assertTrue(pd.isna(cleaned[5]))

    def test_to_num(self):
        s = pd.Series(["$19.99", "1,250", "42", "invalid", None])
        num = to_num(s)
        self.assertEqual(num[0], 19.99)
        self.assertEqual(num[1], 1250.0)
        self.assertEqual(num[2], 42.0)
        self.assertTrue(np.isnan(num[3]))
        self.assertTrue(np.isnan(num[4]))

    def test_inventory_normalize_duplicates_summed(self):
        raw = pd.DataFrame({
            "sku": ["SKU-A", "SKU-A", "SKU-B"],
            "stock": ["10", "15", "5"],
        })
        norm, errors = inventory_normalize(raw, "TestChannel")
        self.assertEqual(len(norm), 2)
        # SKU-A should have 10 + 15 = 25
        sku_a = norm[norm["sku"] == "SKU-A"].iloc[0]
        self.assertEqual(sku_a["testchannel_stock"], 25)
        self.assertTrue(any("duplicated SKU" in e for e in errors))

    def test_reconcile_inventory(self):
        sh = pd.DataFrame({"sku": ["SKU-1", "SKU-2", "SKU-3"], "shopify_stock": [20, 10, 2]})
        am = pd.DataFrame({"sku": ["SKU-1", "SKU-2", "SKU-4"], "amazon_stock": [20, 15, 8]})

        rec = reconcile_inventory(sh, am, low_stock_threshold=5)
        # SKU-1: Match (both 20)
        self.assertEqual(rec[rec["sku"] == "SKU-1"]["status"].iloc[0], "Match")
        # SKU-2: Stock mismatch (10 vs 15)
        self.assertEqual(rec[rec["sku"] == "SKU-2"]["status"].iloc[0], "Stock mismatch")
        # SKU-3: Missing from Amazon (shopify only)
        self.assertEqual(rec[rec["sku"] == "SKU-3"]["status"].iloc[0], "Missing from Amazon")
        # SKU-4: Missing from Shopify (amazon only)
        self.assertEqual(rec[rec["sku"] == "SKU-4"]["status"].iloc[0], "Missing from Shopify")


class TestReorderService(unittest.TestCase):

    def test_calculate_reorder_recommendations_with_moq(self):
        inv = pd.DataFrame({
            "sku": ["SKU-100", "SKU-200"],
            "product_title": ["Product 100", "Product 200"],
            "available_quantity": [0, 50],
            "location_name": ["Warehouse 1", "Warehouse 1"],
        })
        sup = pd.DataFrame({
            "sku": ["SKU-100"],
            "supplier_name": ["Global Vendor"],
            "lead_time_days": [14],
            "minimum_order_qty": [100],  # MOQ is 100
            "reorder_threshold": [10],
            "target_stock": [80],
        })

        reorders, exceptions = calculate_reorder_recommendations(
            inventory_df=inv, supplier_df=sup
        )
        self.assertEqual(len(reorders), 2)

        # SKU-100: stock is 0 <= threshold 10 -> needs reorder
        # target_stock is 80, but MOQ is 100 -> suggested_quantity should be 100!
        row100 = reorders[reorders["sku"] == "SKU-100"].iloc[0]
        self.assertEqual(row100["suggested_quantity"], 100)
        self.assertEqual(row100["review_status"], "Urgent Reorder Required")

        # SKU-200: has no supplier mapping in sup -> should produce an exception
        self.assertTrue(any(e["exception_type"] == "Missing Supplier Mapping" for _, e in exceptions.iterrows()))

    def test_reorder_healthy_stock(self):
        inv = pd.DataFrame([{
            "sku": "SKU-HEALTHY",
            "product_title": "Healthy Item",
            "available_quantity": 25,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-HEALTHY",
            "supplier_name": "Vendor A",
            "reorder_threshold": 10,
            "target_stock": 100,
            "minimum_order_qty": 50,
        }])
        reorders, _ = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertFalse(row["reorder_now"])
        self.assertEqual(row["review_status"], "Healthy")
        self.assertIn("Healthy stock level", row["reason"])
        self.assertEqual(row["supplier_name"], "Vendor A")

    def test_reorder_threshold_boundary_stock(self):
        inv = pd.DataFrame([{
            "sku": "SKU-BOUNDARY",
            "product_title": "Boundary Item",
            "available_quantity": 10,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-BOUNDARY",
            "supplier_name": "Vendor A",
            "reorder_threshold": 10,
            "target_stock": 100,
            "minimum_order_qty": 50,
        }])
        reorders, _ = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertTrue(row["reorder_now"])
        self.assertEqual(row["review_status"], "Reorder Review Needed")
        self.assertIn("at reorder threshold", row["reason"])
        self.assertEqual(row["suggested_quantity"], 90)

    def test_reorder_low_stock(self):
        inv = pd.DataFrame([{
            "sku": "SKU-LOW",
            "product_title": "Low Stock Item",
            "available_quantity": 5,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-LOW",
            "supplier_name": "Vendor A",
            "reorder_threshold": 10,
            "target_stock": 100,
            "minimum_order_qty": 50,
        }])
        reorders, _ = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertTrue(row["reorder_now"])
        self.assertEqual(row["review_status"], "Reorder Review Needed")
        self.assertIn("below reorder threshold", row["reason"])
        self.assertEqual(row["suggested_quantity"], 95)

    def test_reorder_zero_stock_urgent(self):
        inv = pd.DataFrame([{
            "sku": "SKU-ZERO",
            "product_title": "Out of Stock Item",
            "available_quantity": 0,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-ZERO",
            "supplier_name": "Vendor A",
            "reorder_threshold": 10,
            "target_stock": 100,
            "minimum_order_qty": 50,
        }])
        reorders, _ = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertTrue(row["reorder_now"])
        self.assertEqual(row["review_status"], "Urgent Reorder Required")
        self.assertIn("Out of stock", row["reason"])
        self.assertEqual(row["suggested_quantity"], 100)

    def test_moq_enforcement_floor(self):
        inv = pd.DataFrame([{
            "sku": "SKU-MOQ",
            "product_title": "MOQ Test Item",
            "available_quantity": 90,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-MOQ",
            "supplier_name": "Vendor B",
            "reorder_threshold": 10,
            "target_stock": 100,
            "minimum_order_qty": 50,  # Shortfall is 10, MOQ raises it to 50
        }])
        reorders, _ = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertEqual(row["suggested_quantity"], 50)

    def test_missing_supplier_mapping_unassigned(self):
        inv = pd.DataFrame([{
            "sku": "SKU-UNMAPPED",
            "product_title": "Unmapped Item",
            "available_quantity": 3,
        }])
        sup = pd.DataFrame([{
            "sku": "SKU-OTHER",
            "supplier_name": "Vendor C",
        }])
        reorders, exceptions = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)
        row = reorders.iloc[0]
        self.assertEqual(row["supplier_name"], "Unassigned")
        self.assertIn("Missing supplier mapping", row["reason"])
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions.iloc[0]["exception_type"], "Missing Supplier Mapping")


    def test_missing_sku_handling_and_exclusion(self):
        """
        Regression test: Products without SKUs must:
        1. Generate a single 'Missing SKU' exception in exceptions_df.
        2. NOT generate a duplicate 'Missing Supplier Mapping' exception.
        3. Be EXCLUDED from recommendations_df so invalid products don't pollute reorder suggestions.
        """
        inv = pd.DataFrame([
            {"sku": None, "product_title": "Gift Card", "available_quantity": 10},
            {"sku": "", "product_title": "Snowboard No SKU", "available_quantity": 0},
            {"sku": "SKU-VALID-1", "product_title": "Valid SKU Item", "available_quantity": 5},
        ])
        sup = pd.DataFrame([{
            "sku": "SKU-VALID-1",
            "supplier_name": "Acme Supplier",
            "reorder_threshold": 10,
            "target_stock": 50,
            "minimum_order_qty": 20,
        }])

        reorders, exceptions = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)

        # 1. recommendations_df must contain ONLY valid SKUs
        self.assertEqual(len(reorders), 1)
        self.assertEqual(reorders.iloc[0]["sku"], "SKU-VALID-1")
        self.assertEqual(reorders.iloc[0]["supplier_name"], "Acme Supplier")
        self.assertNotIn("Error - Missing SKU", reorders["review_status"].values)

        # 2. exceptions_df must contain exactly 2 Missing SKU exceptions (one for each missing SKU item)
        self.assertEqual(len(exceptions), 2)
        self.assertTrue(all(exceptions["exception_type"] == "Missing SKU"))
        # Must NOT contain any 'Missing Supplier Mapping' exceptions with 'SKU nan'
        self.assertFalse(any(exceptions["exception_type"] == "Missing Supplier Mapping"))

    def test_valid_sku_unmapped_vs_missing_sku(self):
        """
        Confirms difference between:
        - Product without SKU (Missing SKU exception only, excluded from recommendations)
        - Product with valid SKU but not in Supplier Master (Missing Supplier Mapping exception, included in recommendations as Unassigned)
        """
        inv = pd.DataFrame([
            {"sku": None, "product_title": "Item Without SKU", "available_quantity": 10},
            {"sku": "SKU-UNMAPPED-1", "product_title": "Unmapped Valid Item", "available_quantity": 2},
        ])
        sup = pd.DataFrame([{
            "sku": "SKU-OTHER",
            "supplier_name": "Supplier X",
        }])

        reorders, exceptions = calculate_reorder_recommendations(inventory_df=inv, supplier_df=sup)

        # recommendations_df only has the valid SKU
        self.assertEqual(len(reorders), 1)
        self.assertEqual(reorders.iloc[0]["sku"], "SKU-UNMAPPED-1")
        self.assertEqual(reorders.iloc[0]["supplier_name"], "Unassigned")

        # exceptions_df has exactly 2 entries: 1 Missing SKU and 1 Missing Supplier Mapping
        self.assertEqual(len(exceptions), 2)
        exc_types = exceptions["exception_type"].tolist()
        self.assertIn("Missing SKU", exc_types)
        self.assertIn("Missing Supplier Mapping", exc_types)
        unmapped_exc = exceptions[exceptions["exception_type"] == "Missing Supplier Mapping"].iloc[0]
        self.assertEqual(unmapped_exc["sku"], "SKU-UNMAPPED-1")


class TestSyncService(unittest.TestCase):

    def test_run_sync_dry_run(self):
        mock_shopify = MagicMock()
        mock_shopify.store_domain = "test.myshopify.com"
        mock_shopify.fetch_inventory.return_value = pd.DataFrame({
            "sku": ["WATCH-001"],
            "product_title": ["Black Watch"],
            "available_quantity": [2],
            "location_name": ["Primary Location"],
        })

        mock_sheets = MagicMock()
        mock_sheets.is_configured.return_value = True
        mock_sheets.read_supplier_master.return_value = pd.DataFrame({
            "sku": ["WATCH-001"],
            "supplier_name": ["Watch Supplies Co"],
            "lead_time_days": [10],
            "reorder_threshold": [5],
        })

        sync = SyncService(shopify_client=mock_shopify, sheets_client=mock_sheets)
        result = sync.run_sync(publish_to_sheets=False)

        self.assertEqual(result["status"], "PREVIEW_OK")
        self.assertEqual(result["records_read"], 1)
        self.assertEqual(len(result["recommendations_df"]), 1)
        # Should NOT write when publish_to_sheets is False
        mock_sheets.write_inventory_snapshot.assert_not_called()

    def test_run_sync_publish_to_sheets_workflow_and_metrics(self):
        """
        Verifies end-to-end sync publication to Google Sheets:
        - Exact write order: snapshot -> recommendations -> exceptions -> append sync log.
        - Records written metric strictly equals sum of data rows across output tabs (excluding headers).
        - Supplier Master is untouched.
        - Sync Log receives append-only record with matching metrics.
        """
        order_of_calls = []

        mock_shopify = MagicMock()
        mock_shopify.store_domain = "test.myshopify.com"
        # 3 inventory rows: 1 valid mapped, 1 valid unmapped, 1 missing SKU
        mock_shopify.fetch_inventory.return_value = pd.DataFrame({
            "sku": ["SKU-001", "SKU-002", None],
            "product_title": ["Product 1", "Product 2", "Product Without SKU"],
            "available_quantity": [5, 2, 10],
            "location_name": ["Main Warehouse", "Main Warehouse", "Main Warehouse"],
        })

        mock_sheets = MagicMock()
        mock_sheets.is_configured.return_value = True
        mock_sheets.read_supplier_master.side_effect = lambda: (
            order_of_calls.append("read_supplier_master") or pd.DataFrame({
                "sku": ["SKU-001"],
                "supplier_name": ["Vendor Alpha"],
                "lead_time_days": [7],
                "reorder_threshold": [10],
            })
        )
        mock_sheets.write_inventory_snapshot.side_effect = lambda df: (
            order_of_calls.append("write_inventory_snapshot") or len(df)
        )
        mock_sheets.write_reorder_recommendations.side_effect = lambda df: (
            order_of_calls.append("write_reorder_recommendations") or len(df)
        )
        mock_sheets.write_exception_log.side_effect = lambda df: (
            order_of_calls.append("write_exception_log") or len(df)
        )
        mock_sheets.append_sync_log.side_effect = lambda entry: (
            order_of_calls.append("append_sync_log")
        )

        sync = SyncService(shopify_client=mock_shopify, sheets_client=mock_sheets)
        result = sync.run_sync(publish_to_sheets=True)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["records_read"], 3)

        # 3 inventory rows in snapshot, 2 valid SKUs in recommendations, 2 exceptions (1 unmapped + 1 missing SKU)
        # records_written = 3 + 2 + 2 = 7
        self.assertEqual(result["records_written"], 7)

        # Verify exact write order:
        expected_order = [
            "read_supplier_master",
            "write_inventory_snapshot",
            "write_reorder_recommendations",
            "write_exception_log",
            "append_sync_log",
        ]
        self.assertEqual(order_of_calls, expected_order)

        # Verify Sync Log was appended with correct metrics
        mock_sheets.append_sync_log.assert_called_once()
        log_entry = mock_sheets.append_sync_log.call_args[0][0]
        self.assertEqual(log_entry["records_read"], 3)
        self.assertEqual(log_entry["records_written"], 7)
        self.assertEqual(log_entry["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
