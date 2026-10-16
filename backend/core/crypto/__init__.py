from .key_manager import KeyManager
from .jwks_cache import JWKSCache, JWKSError, KeyNotFoundError, RateLimitError
from .nested_token import NestedTokenService, TokenValidationError

_rs_key_manager = None
_cs_key_manager = None
_jwks_cache = None

def get_rs_key_manager() -> KeyManager:
    global _rs_key_manager
    if _rs_key_manager is None:
        _rs_key_manager = KeyManager("resource_server")
    return _rs_key_manager

def get_cs_key_manager() -> KeyManager:
    global _cs_key_manager
    if _cs_key_manager is None:
        _cs_key_manager = KeyManager("confirmation_server")
    return _cs_key_manager

def get_jwks_cache() -> JWKSCache:
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = JWKSCache()
    return _jwks_cache

__all__ = [
    "KeyManager",
    "JWKSCache",
    "JWKSError",
    "KeyNotFoundError",
    "RateLimitError",
    "NestedTokenService",
    "TokenValidationError",
    "get_rs_key_manager",
    "get_cs_key_manager",
    "get_jwks_cache",
]
