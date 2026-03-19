"""Kalshi API client with RSA-PSS authentication.

Handles request signing and provides typed methods for trading operations.
Market data endpoints (GET /markets) are public and don't need auth.
"""

import base64
import logging
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Authenticated Kalshi API client."""

    def __init__(
        self,
        access_key: str | None = None,
        private_key_path: str | None = None,
    ):
        self.access_key = access_key or os.environ.get("KALSHI_ACCESS_KEY", "")
        key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

        if not self.access_key or not key_path:
            raise RuntimeError(
                "KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH must be set in .env"
            )

        key_file = Path(key_path)
        if not key_file.exists():
            raise FileNotFoundError(f"Kalshi private key not found: {key_file}")

        self._private_key = serialization.load_pem_private_key(
            key_file.read_bytes(), password=None
        )
        self._client = httpx.Client(timeout=30)

    def _sign(self, timestamp_ms: int, method: str, path: str) -> str:
        """Create RSA-PSS signature for a request."""
        # Strip query params for signing
        path_clean = path.split("?")[0]
        message = f"{timestamp_ms}{method}{path_clean}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, path: str) -> dict:
        """Generate authentication headers for a request."""
        ts = int(time.time() * 1000)
        sig = self._sign(ts, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.access_key,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        """Make an authenticated request to the Kalshi API."""
        # Signing requires the full path including /trade-api/v2 prefix
        full_path = f"/trade-api/v2{path}"
        url = f"https://api.elections.kalshi.com{full_path}"
        headers = self._auth_headers(method, full_path)
        logger.debug("%s %s", method, url)
        resp = self._client.request(method, url, headers=headers, json=json)
        resp.raise_for_status()
        return resp.json()

    def get_balance(self) -> dict:
        """Get account balance. Returns balance and portfolio_value in cents."""
        return self._request("GET", "/portfolio/balance")

    def create_order(
        self,
        ticker: str,
        side: str,
        action: str = "buy",
        count: int = 1,
        yes_price_dollars: str | None = None,
        no_price_dollars: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """Place an order on a market.

        Args:
            ticker: Market ticker (e.g., "KXNCAAMBGAME-26MAR19SIEDUKE-DUKE")
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            yes_price_dollars: Limit price for yes side (e.g., "0.56")
            no_price_dollars: Limit price for no side (e.g., "0.44")
            client_order_id: Optional user-provided order ID
        """
        body: dict = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
        }
        if yes_price_dollars:
            body["yes_price_dollars"] = yes_price_dollars
        if no_price_dollars:
            body["no_price_dollars"] = no_price_dollars
        if client_order_id:
            body["client_order_id"] = client_order_id

        return self._request("POST", "/portfolio/orders", json=body)

    def get_orders(self, ticker: str | None = None, status: str | None = None) -> dict:
        """Get orders, optionally filtered by ticker or status."""
        params = []
        if ticker:
            params.append(f"ticker={ticker}")
        if status:
            params.append(f"status={status}")
        path = "/portfolio/orders"
        if params:
            path += "?" + "&".join(params)
        return self._request("GET", path)

    def get_positions(self) -> dict:
        """Get current positions."""
        return self._request("GET", "/portfolio/positions")
