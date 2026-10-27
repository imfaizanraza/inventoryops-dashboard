import os
import unittest
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sample_data"))

class TestInventoryOpsDashboard(unittest.TestCase):
    def test_initial_load_no_errors(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        self.assertFalse(at.exception, "App crashed on load")
        self.assertEqual(len(at.error), 0, "Errors displayed on empty dashboard")

    def test_single_amazon_upload_no_false_errors(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        with open(os.path.join(DATA_DIR, "amazon_inventory.csv"), "rb") as f:
            at.file_uploader(key="amazon").upload("amazon_inventory.csv", f.read())
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0, f"False errors shown on single upload: {[e.value for e in at.error]}")

    def test_single_shopify_upload_no_false_errors(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        with open(os.path.join(DATA_DIR, "shopify_inventory.csv"), "rb") as f:
            at.file_uploader(key="shopify").upload("shopify_inventory.csv", f.read())
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0, f"False errors shown on single upload: {[e.value for e in at.error]}")

    def test_both_inventory_comparison(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        with open(os.path.join(DATA_DIR, "amazon_inventory.csv"), "rb") as f:
            at.file_uploader(key="amazon").upload("amazon_inventory.csv", f.read())
        with open(os.path.join(DATA_DIR, "shopify_inventory.csv"), "rb") as f:
            at.file_uploader(key="shopify").upload("shopify_inventory.csv", f.read())
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0)

        df = at.dataframe[0].value
        # Check stock mismatch and missing items
        mismatch_row = df[df["sku"] == "WATCH-002"].iloc[0]
        self.assertEqual(mismatch_row["status"], "Stock mismatch")

        missing_sh = df[df["sku"] == "WATCH-004"].iloc[0]
        self.assertEqual(missing_sh["status"], "Missing from Shopify")

        missing_am = df[df["sku"] == "WATCH-005"].iloc[0]
        self.assertEqual(missing_am["status"], "Missing from Amazon")

    def test_orders_exceptions(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        with open(os.path.join(DATA_DIR, "orders.csv"), "rb") as f:
            at.file_uploader(key="orders").upload("orders.csv", f.read())
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0)

        df = at.dataframe[0].value
        checks = df["order_check"].tolist()
        self.assertTrue(any("Missing tracking" in c for c in checks))
        self.assertTrue(any("Not yet fulfilled" in c for c in checks))

    def test_sales_and_suppliers_suggestions(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        with open(os.path.join(DATA_DIR, "sales_history.csv"), "rb") as f:
            at.file_uploader(key="sales").upload("sales_history.csv", f.read())
        with open(os.path.join(DATA_DIR, "suppliers.csv"), "rb") as f:
            at.file_uploader(key="suppliers").upload("suppliers.csv", f.read())
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0)

        df = at.dataframe[0].value
        self.assertIn("suggested_reorder_qty", df.columns)
        self.assertIn("reorder_now", df.columns)

    def test_invalid_csv_handled_gracefully(self):
        at = AppTest.from_file(APP_PATH, default_timeout=10)
        at.run()
        at.file_uploader(key="shopify").upload("invalid.csv", b"col_a,col_b\n1,2\n")
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(len(at.warning) > 0)
        self.assertIn("required columns not found", at.warning[0].value)

if __name__ == "__main__":
    unittest.main()
