# Helfen Operations Hub — Setup & Configuration Guide

This guide walks you through setting up your local environment, creating your Shopify Development Store and Dev Dashboard App, and configuring credentials locally.

---

## 1. Local Environment Configuration

Copy the template [.env.example](file:///Users/faizan/Projects/inventoryops_dashboard/.env.example) to `.env`:

```bash
cp .env.example .env
```

Your `.env` file is git-ignored and stays strictly on your local machine. Never commit `.env` or share your secrets.

---

## 2. Setting Up Your Shopify Development Store & App

### Step 1: Access Shopify Dev Dashboard / Partner Account
1. Go to [https://partners.shopify.com](https://partners.shopify.com) or [https://dev.shopify.com](https://dev.shopify.com).
2. Log in with your Shopify account (or sign up for a free Partner account if you don't have one).

### Step 2: Create a Development Store (for test data)
1. In your Partner Dashboard sidebar, click **Stores** > **Add store** > **Create development store**.
2. Select **Create a store for testing or building**.
3. Enter a store name (e.g., `helfen-ops-test`). Your store URL will look like:
   `helfen-ops-test.myshopify.com`
4. Choose **Developer preview** or standard test data (you can add a few sample products with SKUs and inventory).
5. Click **Create development store**.

### Step 3: Create an App in the Dev Dashboard
1. In your Partner/Dev Dashboard, click **Apps** in the left sidebar.
2. Click **Create app**.
3. Choose **Create app manually**.
4. Give your app a name (e.g., `Helfen Operations Connector`).

### Step 4: Configure API Access Scopes (Read-Only)
1. Inside your app page, navigate to **Configuration** or **API access**.
2. Locate **Admin API integration** and click **Configure**.
3. Select ONLY the following two read-only scopes:
   - `read_products`
   - `read_inventory`
   *(Do NOT select write scopes or customer/order scopes).*
4. Click **Save**.

### Step 5: Install App on Your Development Store & Obtain Credentials
1. Go to the **API credentials** tab in your app.
2. Under **Client credentials grant**, ensure your development store is selected in the same organization.
3. Install the app on your test store.
4. Copy the following three values into your local `.env` file:
   ```env
   SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
   SHOPIFY_CLIENT_ID=your_client_id_here
   SHOPIFY_CLIENT_SECRET=your_client_secret_here
   SHOPIFY_API_VERSION=2026-10
   ```

---

## 3. Verifying Shopify in the Dashboard

1. Launch your dashboard:
   ```bash
   streamlit run app.py
   ```
2. In the sidebar, select **Shopify API** mode.
3. In Tab 0 (**Overview**), click **🔍 Test Shopify Connection**.
4. Once verified, click **📥 Fetch Shopify Inventory** to inspect your store's live catalog, variants, and stock balances directly in the UI!
