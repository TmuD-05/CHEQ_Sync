import time
import json
import logging
import requests
from typing import Dict, List, Optional
from jwcrypto import jwk

logger = logging.getLogger(__name__)

class JWKSError(Exception):
    """Base exception for JWKS operations."""
    pass

class KeyNotFoundError(JWKSError):
    """Raised when the specified kid cannot be found in the JWKS."""
    pass

class RateLimitError(JWKSError):
    """Raised when an on-demand JWKS cache refresh is attempted too soon (anti-DoS)."""
    pass

class JWKSCache:
    """
    In-memory cache for remote JWKS sets with TTL and Anti-DoS rate limiting.
    
    Prevents socket exhaustion and upstream flooding by enforcing a cooldown
    on on-demand cache-busting refreshes.
    """
    def __init__(self, cache_ttl_seconds: int = 21600, cooldown_seconds: int = 60):
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        # Structure: { jwks_url: {"keys": [JWK], "fetched_at": float} }
        self._cache: Dict[str, Dict] = {}

    def register_jwks(self, jwks_url: str, jwks_data: dict):
        """
        Manually pre-registers a JWKS dictionary into cache.
        Useful for testing or local in-process service lookups.
        """
        keys = []
        for key_dict in jwks_data.get("keys", []):
            try:
                k = jwk.JWK()
                k.import_key(**key_dict)
                keys.append(k)
            except Exception as e:
                logger.warning(f"Failed to import JWK {key_dict.get('kid')}: {e}")
        self._cache[jwks_url] = {
            "keys": keys,
            "fetched_at": time.time()
        }

    def _fetch_remote_jwks(self, jwks_url: str) -> List[jwk.JWK]:
        logger.info(f"Fetching remote JWKS from: {jwks_url}")
        resp = requests.get(jwks_url, timeout=5)
        resp.raise_for_status()
        jwks_data = resp.json()
        keys = []
        for key_dict in jwks_data.get("keys", []):
            try:
                k = jwk.JWK()
                k.import_key(**key_dict)
                keys.append(k)
            except Exception as e:
                logger.warning(f"Failed to import JWK {key_dict.get('kid')}: {e}")
        return keys

    def get_key(self, jwks_url: str, kid: Optional[str] = None, use: Optional[str] = None) -> jwk.JWK:
        """
        Retrieves a public JWK matching the kid (and optional use) from the given jwks_url.
        If kid is None, retrieves the first matching key for the specified use (e.g., use="enc").
        Uses in-memory cache if available and fresh. Refreshes cache if key is missing or expired,
        subject to strict anti-DoS rate limiting.
        """
        if kid is None and use is None:
            raise ValueError("At least one of 'kid' or 'use' must be specified.")

        now = time.time()
        entry = self._cache.get(jwks_url)

        # 1. Check if key is already in cache and cache is not completely expired
        if entry:
            for k in entry["keys"]:
                if (kid is None or k.key_id == kid) and (use is None or k.get("use") == use):
                    # Found valid key in cache
                    return k

        # 2. Key not in cache or cache missing. Must attempt refresh if allowed by rate limiter.
        last_fetched = entry["fetched_at"] if entry else 0
        elapsed_since_last_fetch = now - last_fetched

        if elapsed_since_last_fetch < self.cooldown_seconds:
            # Throttling active: protect against DoS attack / rapid bursts of invalid kid
            raise RateLimitError(
                f"Rate limit exceeded for JWKS refresh from {jwks_url}. "
                f"Cooldown active ({elapsed_since_last_fetch:.1f}s / {self.cooldown_seconds}s)."
            )

        # 3. Perform remote fetch
        try:
            keys = self._fetch_remote_jwks(jwks_url)
            self._cache[jwks_url] = {
                "keys": keys,
                "fetched_at": now
            }
        except Exception as e:
            if isinstance(e, JWKSError):
                raise e
            raise JWKSError(f"Failed to fetch JWKS from {jwks_url}: {e}")

        # 4. Search updated keys
        for k in keys:
            if (kid is None or k.key_id == kid) and (use is None or k.get("use") == use):
                return k

        raise KeyNotFoundError(f"Key with kid '{kid}' (use={use}) not found in JWKS from {jwks_url}")
