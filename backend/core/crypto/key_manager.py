import os
import time
import json
import logging
from typing import Optional, List, Dict, Any
from jwcrypto import jwk

logger = logging.getLogger(__name__)

class KeyManager:
    """
    Manages in-memory cryptographic key pairs for signing (JWS) and encryption (JWE),
    enforcing key separation according to RFC 7517/7518.
    
    Keys are held strictly in RAM without reading from static disk PEM files.
    Supports runtime zero-downtime key rotation with an in-memory grace-period pool.
    """
    def __init__(self, service_name: str, key_size: int = 2048):
        self.service_name = service_name
        self.key_size = key_size
        self._sig_version = 1
        self._enc_version = 1
        self.sig_kid = f"{service_name}-sig-v1"
        self.enc_kid = f"{service_name}-enc-v1"

        # Active in-memory keys
        self._signing_key = self._load_or_generate_key(
            key_type="sig",
            kid=self.sig_kid,
            alg="RS256",
            use="sig"
        )
        self._encryption_key = self._load_or_generate_key(
            key_type="enc",
            kid=self.enc_kid,
            alg="RSA-OAEP-256",
            use="enc"
        )

        # Grace-period pools: [{"key": JWK, "retire_at": timestamp}]
        self._retired_signing_keys: List[Dict[str, Any]] = []
        self._retired_encryption_keys: List[Dict[str, Any]] = []

    def _generate_rsa_key(self, kid: str, alg: str, use: str) -> jwk.JWK:
        logger.info(f"Generating new in-memory {self.service_name} {use} RSA key ({kid}).")
        return jwk.JWK.generate(
            kty="RSA",
            size=self.key_size,
            kid=kid,
            use=use,
            alg=alg
        )

    def _load_or_generate_key(self, key_type: str, kid: str, alg: str, use: str) -> jwk.JWK:
        prefix = self.service_name.upper()
        env_var_name = f"{prefix}_{key_type.upper()}_KEY"
        pem_data = os.environ.get(env_var_name)

        if not pem_data and key_type == "sig":
            pem_data = os.environ.get(f"{prefix}_PRIVATE_KEY")

        if pem_data:
            try:
                key = jwk.JWK.from_pem(pem_data.encode("utf-8"))
                key["kid"] = kid
                key["use"] = use
                key["alg"] = alg
                logger.info(f"Loaded {self.service_name} {key_type} key from environment variable.")
                return key
            except Exception as e:
                logger.error(f"Failed to parse PEM from environment variable for {self.service_name} {key_type}: {e}")
                raise ValueError(f"Invalid private key in environment variable {env_var_name}: {e}")

        # Pure in-memory generation (Zero disk reads)
        return self._generate_rsa_key(kid=kid, alg=alg, use=use)

    def _cleanup_retired_keys(self):
        now = time.time()
        self._retired_signing_keys = [
            item for item in self._retired_signing_keys if item["retire_at"] > now
        ]
        self._retired_encryption_keys = [
            item for item in self._retired_encryption_keys if item["retire_at"] > now
        ]

    def rotate_signing_key(self, grace_period_seconds: int = 900) -> str:
        """
        Rotates the active signing key:
        1. Archives current active key into the retired pool with a grace-period expiration.
        2. Generates a new active key in RAM.
        3. Returns the new kid.
        """
        self._cleanup_retired_keys()
        self._retired_signing_keys.append({
            "key": self._signing_key,
            "retire_at": time.time() + grace_period_seconds
        })

        self._sig_version += 1
        new_kid = f"{self.service_name}-sig-v{self._sig_version}"
        self._signing_key = self._generate_rsa_key(kid=new_kid, alg="RS256", use="sig")
        self.sig_kid = new_kid
        logger.info(f"Rotated signing key for {self.service_name} to {new_kid}. Old key archived for {grace_period_seconds}s.")
        return new_kid

    def rotate_encryption_key(self, grace_period_seconds: int = 900) -> str:
        """
        Rotates the active encryption key:
        1. Archives current active key into the retired pool with a grace-period expiration.
        2. Generates a new active key in RAM.
        3. Returns the new kid.
        """
        self._cleanup_retired_keys()
        self._retired_encryption_keys.append({
            "key": self._encryption_key,
            "retire_at": time.time() + grace_period_seconds
        })

        self._enc_version += 1
        new_kid = f"{self.service_name}-enc-v{self._enc_version}"
        self._encryption_key = self._generate_rsa_key(kid=new_kid, alg="RSA-OAEP-256", use="enc")
        self.enc_kid = new_kid
        logger.info(f"Rotated encryption key for {self.service_name} to {new_kid}. Old key archived for {grace_period_seconds}s.")
        return new_kid

    @property
    def signing_key(self) -> jwk.JWK:
        """Returns the current active private signing key (in RAM)."""
        return self._signing_key

    @property
    def encryption_key(self) -> jwk.JWK:
        """Returns the current active private encryption key (in RAM)."""
        return self._encryption_key

    def get_key_by_kid(self, kid: str) -> Optional[jwk.JWK]:
        """
        Finds a private key matching the given kid across active and unexpired retired keys.
        """
        self._cleanup_retired_keys()
        if self._signing_key.key_id == kid:
            return self._signing_key
        if self._encryption_key.key_id == kid:
            return self._encryption_key

        for item in self._retired_signing_keys:
            if item["key"].key_id == kid:
                return item["key"]

        for item in self._retired_encryption_keys:
            if item["key"].key_id == kid:
                return item["key"]

        return None

    def export_signing_private_pem(self) -> bytes:
        """Exports the active signing private key as PEM bytes."""
        return self._signing_key.export_to_pem(private_key=True, password=None)

    def export_signing_public_pem(self) -> bytes:
        """Exports the active signing public key as PEM bytes."""
        return self._signing_key.export_to_pem(private_key=False)

    def export_encryption_private_pem(self) -> bytes:
        """Exports the active encryption private key as PEM bytes."""
        return self._encryption_key.export_to_pem(private_key=True, password=None)

    def export_encryption_public_pem(self) -> bytes:
        """Exports the active encryption public key as PEM bytes."""
        return self._encryption_key.export_to_pem(private_key=False)

    def get_public_jwks(self) -> dict:
        """
        Returns the RFC 7517 JWKS dictionary containing all public keys
        (active keys + unexpired retired keys in grace period).
        """
        self._cleanup_retired_keys()
        keys = [
            json.loads(self._signing_key.export_public()),
            json.loads(self._encryption_key.export_public())
        ]

        for item in self._retired_signing_keys:
            keys.append(json.loads(item["key"].export_public()))

        for item in self._retired_encryption_keys:
            keys.append(json.loads(item["key"].export_public()))

        return {"keys": keys}
