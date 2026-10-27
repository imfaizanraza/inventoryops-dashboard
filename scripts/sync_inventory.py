#!/usr/bin/env python3
"""
CLI Synchronization Runner for Helfen Operations Hub.

Usage:
    python scripts/sync_inventory.py
    python scripts/sync_inventory.py --dry-run
    python scripts/sync_inventory.py --no-sheets --verbose

Exit Codes:
    0: Synchronization succeeded or completed dry-run.
    1: Synchronization failed.
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# Ensure project root is in python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from integrations.shopify_client import ShopifyClient
from integrations.google_sheets_client import GoogleSheetsClient
from services.sync_service import SyncService


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run automated inventory synchronization from Shopify to Google Sheets."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and compute recommendations without writing changes to Google Sheets.",
    )
    parser.add_argument(
        "--no-sheets",
        action="store_true",
        help="Skip publishing to Google Sheets even if configured.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print("=" * 60)
    print("📦 Helfen Operations Hub — Inventory Synchronization")
    print("=" * 60)

    shopify = ShopifyClient()
    sheets = GoogleSheetsClient()

    if not shopify.is_configured():
        missing = ", ".join(shopify.get_missing_config_fields())
        print(f"❌ Error: Shopify is not configured. Missing: {missing}")
        print("   Please check your .env file or environment variables.")
        return 1

    publish_to_sheets = not (args.dry_run or args.no_sheets)
    if publish_to_sheets and not sheets.is_configured():
        print("⚠️ Warning: Google Sheets is not fully configured.")
        print("   Running in local computation mode only.")
        publish_to_sheets = False

    service = SyncService(shopify_client=shopify, sheets_client=sheets)
    result = service.run_sync(
        publish_to_sheets=publish_to_sheets,
        trigger="cli",
    )

    print(f"\nRun ID:          {result['run_id']}")
    print(f"Started (UTC):   {result['started_at_utc']}")
    print(f"Finished (UTC):  {result['finished_at_utc']}")
    print(f"Status:          {result['status']}")
    print(f"Records Read:    {result['records_read']}")
    print(f"Records Written: {result['records_written']}")

    if result["error_summary"]:
        print(f"Error Summary:   {result['error_summary']}")

    if result["status"] == "FAILED":
        print("\n❌ Synchronization failed.")
        return 1

    print("\n✅ Synchronization completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
