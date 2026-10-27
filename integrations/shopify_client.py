"""
Shopify Admin GraphQL Client.

Implements the official Shopify Client Credentials Grant (OAuth) and GraphQL Admin API
connector for read-only retrieval of products, variants, locations, and inventory levels.

Security:
- Never logs or exposes client secrets or access tokens.
- Sanitizes error messages to protect credentials.
- Strictly read-only queries; zero write or mutation operations.
"""

from datetime import datetime, timezone, timedelta
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
import requests

from services.inventory_service import normalize_shopify_graphql_products
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "2026-10"
DEFAULT_TIMEOUT_SECONDS = 15


class ShopifyAPIError(Exception):
    """Base exception for Shopify API errors with sanitized details."""
    pass


class ShopifyClient:
    """
    Client for interacting with Shopify Admin GraphQL API using Client Credentials Grant.
    """

    def __init__(
        self,
        store_domain: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        raw_domain = store_domain if store_domain is not None else os.getenv("SHOPIFY_STORE_DOMAIN", "")
        # Strip protocol, whitespace, or trailing slash if user entered https://...
        self.store_domain = (
            raw_domain.replace("https://", "").replace("http://", "").strip().rstrip("/")
        )
        self.client_id = client_id if client_id is not None else os.getenv("SHOPIFY_CLIENT_ID", "")
        self.client_secret = client_secret if client_secret is not None else os.getenv("SHOPIFY_CLIENT_SECRET", "")
        self.api_version = api_version if api_version is not None else os.getenv("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
        self.timeout = timeout

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def is_configured(self) -> bool:
        """Returns True if store domain and client credentials are non-empty."""
        return bool(self.store_domain and self.client_id and self.client_secret)

    def get_missing_config_fields(self) -> List[str]:
        """Returns a list of missing configuration fields for user feedback."""
        missing = []
        if not self.store_domain:
            missing.append("SHOPIFY_STORE_DOMAIN")
        if not self.client_id:
            missing.append("SHOPIFY_CLIENT_ID")
        if not self.client_secret:
            missing.append("SHOPIFY_CLIENT_SECRET")
        return missing

    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Retrieves a valid access token using the Client Credentials grant.
        Reuses cached token if still valid with a 60-second safety buffer.
        """
        if not self.is_configured():
            missing = ", ".join(self.get_missing_config_fields())
            raise ShopifyAPIError(
                f"Shopify client is not configured. Missing environment variables: {missing}"
            )

        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._access_token
            and self._token_expires_at
            and self._token_expires_at > (now + timedelta(seconds=60))
        ):
            return self._access_token

        token_url = f"https://{self.store_domain}/admin/oauth/access_token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resp = requests.post(token_url, json=payload, headers=headers, timeout=self.timeout)
        except requests.exceptions.Timeout:
            raise ShopifyAPIError(f"Connection timed out contacting Shopify at {self.store_domain}")
        except requests.exceptions.RequestException as e:
            raise ShopifyAPIError(f"Network error contacting Shopify: {e.__class__.__name__}")

        if resp.status_code == 401 or resp.status_code == 400:
            raise ShopifyAPIError(
                "Authentication failed. Please check that SHOPIFY_CLIENT_ID and "
                "SHOPIFY_CLIENT_SECRET match your Dev Dashboard app and belong to the same organization."
            )
        elif resp.status_code != 200:
            raise ShopifyAPIError(
                f"Shopify OAuth token endpoint returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
        except Exception:
            raise ShopifyAPIError("Failed to parse JSON response from Shopify OAuth endpoint.")

        token = data.get("access_token")
        if not token:
            raise ShopifyAPIError(
                f"Shopify OAuth response did not contain access_token: {data.get('error_description', 'Unknown error')}"
            )

        expires_in = data.get("expires_in", 86400)
        self._access_token = token
        self._token_expires_at = now + timedelta(seconds=int(expires_in))
        return self._access_token

    def execute_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes an authenticated GraphQL Admin API query.
        """
        token = self.get_access_token()
        graphql_url = f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": token,
        }
        body: Dict[str, Any] = {"query": query}
        if variables:
            body["variables"] = variables

        try:
            resp = requests.post(graphql_url, json=body, headers=headers, timeout=self.timeout)
        except requests.exceptions.Timeout:
            raise ShopifyAPIError("GraphQL request timed out.")
        except requests.exceptions.RequestException as e:
            raise ShopifyAPIError(f"Network error during GraphQL execution: {e.__class__.__name__}")

        if resp.status_code == 401:
            # Token might be invalidated; clear cached token
            self._access_token = None
            raise ShopifyAPIError("Shopify returned 401 Unauthorized. Access token expired or invalid.")
        elif resp.status_code == 403:
            raise ShopifyAPIError(
                "Shopify returned 403 Forbidden. Your app is missing required access scopes. "
                "Ensure 'read_products' and 'read_inventory' are enabled."
            )
        elif resp.status_code == 429:
            raise ShopifyAPIError("Shopify API rate limit exceeded. Please retry after a brief delay.")
        elif resp.status_code != 200:
            raise ShopifyAPIError(f"Shopify GraphQL endpoint returned HTTP {resp.status_code}")

        try:
            result = resp.json()
        except Exception:
            raise ShopifyAPIError("Could not parse JSON response from Shopify GraphQL endpoint.")

        if "errors" in result and result["errors"]:
            err_msgs = [e.get("message", "Unknown error") for e in result["errors"]]
            raise ShopifyAPIError(f"Shopify GraphQL Error: {'; '.join(err_msgs)}")

        return result.get("data", {})

    def test_connection(self) -> Dict[str, Any]:
        """
        Validates credentials and network connectivity by querying shop metadata.
        """
        query = """
        query TestConnection {
            shop {
                name
                myshopifyDomain
                currencyCode
                plan {
                    displayName
                }
            }
        }
        """
        data = self.execute_graphql(query)
        shop = data.get("shop", {})
        return {
            "success": True,
            "shop_name": shop.get("name", "Unknown Shop"),
            "domain": shop.get("myshopifyDomain", self.store_domain),
            "currency": shop.get("currencyCode", "USD"),
            "plan": shop.get("plan", {}).get("displayName", "Development"),
        }

    def fetch_products_page(
        self, first: int = 50, after: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
        """
        Fetches one page of products with variants and inventory levels.
        Returns: (nodes, has_next_page, end_cursor)
        """
        query = """
        query GetProductsAndInventory($first: Int!, $after: String) {
            products(first: $first, after: $after) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    id
                    title
                    status
                    variants(first: 50) {
                        nodes {
                            id
                            title
                            sku
                            inventoryItem {
                                id
                                tracked
                                inventoryLevels(first: 10) {
                                    nodes {
                                        location {
                                            id
                                        }
                                        quantities(names: ["available"]) {
                                            name
                                            quantity
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        variables: Dict[str, Any] = {"first": first}
        if after:
            variables["after"] = after

        data = self.execute_graphql(query, variables)
        products_conn = data.get("products", {})
        nodes = products_conn.get("nodes", [])
        page_info = products_conn.get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        end_cursor = page_info.get("endCursor")
        return nodes, has_next, end_cursor

    def fetch_inventory(self, max_pages: int = 20) -> pd.DataFrame:
        """
        Iterates through all pages of products in the store and returns a
        normalized DataFrame of inventory at the location and SKU level.
        """
        all_nodes = []
        has_next = True
        cursor = None
        pages_read = 0

        while has_next and pages_read < max_pages:
            nodes, has_next, cursor = self.fetch_products_page(first=50, after=cursor)
            all_nodes.extend(nodes)
            pages_read += 1

        normalized_df = normalize_shopify_graphql_products(all_nodes)
        return normalized_df
