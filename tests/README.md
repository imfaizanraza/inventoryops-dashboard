# Manual test checklist

1. Upload sample_data/shopify_inventory.csv and sample_data/amazon_inventory.csv.
2. Confirm WATCH-002 is a stock mismatch.
3. Confirm WATCH-004 is missing from Shopify and WATCH-005 is missing from Amazon.
4. Upload orders.csv and confirm missing tracking, unfulfilled, and duplicate order ID warnings.
5. Upload sales_history.csv and suppliers.csv and inspect reorder suggestions.
6. Remove a required column from a copy of an inventory file and confirm the dashboard reports the missing columns.
7. Run the same uploads twice and confirm results are repeatable.
