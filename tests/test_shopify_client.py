"""
Tests for Shopify GraphQL Admin API Client.
Uses mocked HTTP responses to test all queries, pagination, and error scenarios offline.
"""

from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import requests

from integrations.shopify_client import ShopifyClient, ShopifyAPIError


class TestShopifyClient(unittest.TestCase):

    def setUp(self):
        self.client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            client_id="test_client_id",
            client_secret="test_client_secret",
            api_version="2026-10",
        )

    def test_configuration_detection(self):
        self.assertTrue(self.client.is_configured())
        self.assertEqual(len(self.client.get_missing_config_fields()), 0)

        unconfigured = ShopifyClient(store_domain="", client_id="", client_secret="")
        self.assertFalse(unconfigured.is_configured())
        self.assertEqual(len(unconfigured.get_missing_config_fields()), 3)

    @patch("integrations.shopify_client.requests.post")
    def test_get_access_token_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "shpat_mock_token_12345",
            "scope": "read_products,read_inventory",
            "expires_in": 86400,
        }
        mock_post.return_value = mock_resp

        token = self.client.get_access_token()
        self.assertEqual(token, "shpat_mock_token_12345")
        mock_post.assert_called_once()

        # Test token caching (subsequent call should NOT make another HTTP request)
        cached_token = self.client.get_access_token()
        self.assertEqual(cached_token, "shpat_mock_token_12345")
        self.assertEqual(mock_post.call_count, 1)

    @patch("integrations.shopify_client.requests.post")
    def test_get_access_token_rejected_credentials(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        with self.assertRaises(ShopifyAPIError) as ctx:
            self.client.get_access_token()
        self.assertIn("Authentication failed", str(ctx.exception))

    @patch("integrations.shopify_client.requests.post")
    def test_test_connection_success(self, mock_post):
        # First call: OAuth token
        mock_token_resp = MagicMock(status_code=200)
        mock_token_resp.json.return_value = {"access_token": "mock_token", "expires_in": 3600}

        # Second call: GraphQL shop query
        mock_gql_resp = MagicMock(status_code=200)
        mock_gql_resp.json.return_value = {
            "data": {
                "shop": {
                    "name": "Test Helfen Store",
                    "myshopifyDomain": "test-store.myshopify.com",
                    "currencyCode": "USD",
                    "plan": {"displayName": "Development"},
                }
            }
        }
        mock_post.side_effect = [mock_token_resp, mock_gql_resp]

        res = self.client.test_connection()
        self.assertTrue(res["success"])
        self.assertEqual(res["shop_name"], "Test Helfen Store")
        self.assertEqual(res["domain"], "test-store.myshopify.com")

    @patch("integrations.shopify_client.requests.post")
    def test_graphql_errors_handled(self, mock_post):
        mock_token_resp = MagicMock(status_code=200)
        mock_token_resp.json.return_value = {"access_token": "mock_token", "expires_in": 3600}

        mock_gql_resp = MagicMock(status_code=200)
        mock_gql_resp.json.return_value = {
            "errors": [{"message": "Field 'nonexistent' doesn't exist on type 'Shop'"}]
        }
        mock_post.side_effect = [mock_token_resp, mock_gql_resp]

        with self.assertRaises(ShopifyAPIError) as ctx:
            self.client.execute_graphql("query { nonexistent }")
        self.assertIn("Field 'nonexistent' doesn't exist", str(ctx.exception))

    @patch("integrations.shopify_client.requests.post")
    def test_pagination_and_inventory_normalization(self, mock_post):
        # 1. Token response
        mock_token_resp = MagicMock(status_code=200)
        mock_token_resp.json.return_value = {"access_token": "mock_token", "expires_in": 3600}

        # 2. Page 1 GraphQL response (has next page)
        mock_page1_resp = MagicMock(status_code=200)
        mock_page1_resp.json.return_value = {
            "data": {
                "products": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor_1"},
                    "nodes": [
                        {
                            "id": "gid://shopify/Product/1",
                            "title": "Minimal Watch",
                            "status": "ACTIVE",
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/101",
                                        "title": "Black Strap",
                                        "sku": "WATCH-001",
                                        "inventoryItem": {
                                            "id": "gid://shopify/InventoryItem/501",
                                            "tracked": True,
                                            "inventoryLevels": {
                                                "nodes": [
                                                    {
                                                        "location": {
                                                            "id": "gid://shopify/Location/10",
                                                            "name": "Main Warehouse",
                                                        },
                                                        "quantities": [{"name": "available", "quantity": 25}],
                                                    }
                                                ]
                                            },
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                }
            }
        }

        # 3. Page 2 GraphQL response (last page)
        mock_page2_resp = MagicMock(status_code=200)
        mock_page2_resp.json.return_value = {
            "data": {
                "products": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "gid://shopify/Product/2",
                            "title": "Leather Strap",
                            "status": "ACTIVE",
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/102",
                                        "title": "Brown",
                                        "sku": "WATCH-002",
                                        "inventoryItem": {
                                            "id": "gid://shopify/InventoryItem/502",
                                            "tracked": True,
                                            "inventoryLevels": {
                                                "nodes": [
                                                    {
                                                        "location": {
                                                            "id": "gid://shopify/Location/10",
                                                            "name": "Main Warehouse",
                                                        },
                                                        "quantities": [{"name": "available", "quantity": 8}],
                                                    }
                                                ]
                                            },
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                }
            }
        }

        mock_post.side_effect = [mock_token_resp, mock_page1_resp, mock_page2_resp]

        df = self.client.fetch_inventory()
        self.assertEqual(len(df), 2)
        self.assertListEqual(df["sku"].tolist(), ["WATCH-001", "WATCH-002"])
        self.assertEqual(df[df["sku"] == "WATCH-001"]["available_quantity"].iloc[0], 25)
        self.assertEqual(df[df["sku"] == "WATCH-002"]["available_quantity"].iloc[0], 8)
        self.assertEqual(df["location_name"].iloc[0], "Main Warehouse")


if __name__ == "__main__":
    unittest.main()
