"""Local EC key pair handling for the Apple Ads Platform API v1.

The v1 API authenticates with OAuth2 client credentials: the user uploads
a PUBLIC key in the Apple Ads UI (Account Settings > API) and RespectASO
signs short-lived ES256 JWTs with the matching PRIVATE key (api.py).

The private key is generated locally and stored in its own file:

    DATA_DIR/apple_ads_private_key.pem   (chmod 600)

It deliberately does NOT live inside the shared settings.json: the key is
a long-lived secret, settings.json is a multi-owner read-modify-write
document, and a separate file keeps the "your key never leaves this
machine" promise auditable. The key leaves the machine only as ES256
signatures on JWTs sent to Apple's token endpoint.

This module lives in the free-tier `aso` app and must not import aso_pro.
"""

import logging
import os
import stat

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings

logger = logging.getLogger(__name__)

KEY_FILENAME = "apple_ads_private_key.pem"


class AppleKeyError(Exception):
    """Raised when a private key is missing, unreadable, or the wrong type."""


def _key_path():
    return settings.DATA_DIR / KEY_FILENAME


def has_private_key() -> bool:
    return _key_path().exists()


def generate_key_pair() -> str:
    """Generate a new EC P-256 private key, store it, return the PUBLIC key PEM.

    Overwrites any existing key file - callers must confirm with the user
    first (a new key invalidates the one uploaded to Apple).
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    save_private_key(pem)
    return public_key_pem()


def save_private_key(pem: str) -> None:
    """Validate and persist a private key PEM with owner-only permissions."""
    validate_private_key(pem)
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pem, encoding="ascii")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Apple Ads private key saved to %s", path)


def load_private_key_pem() -> str:
    """Return the stored private key PEM.

    Raises:
        AppleKeyError: when no key has been generated or imported yet.
    """
    path = _key_path()
    if not path.exists():
        raise AppleKeyError(
            "No Apple Ads private key found - generate or import one first."
        )
    return path.read_text(encoding="ascii")


def delete_private_key() -> None:
    path = _key_path()
    if path.exists():
        path.unlink()
        logger.info("Apple Ads private key deleted.")


def public_key_pem() -> str:
    """Derive the public key PEM (what the user pastes into Apple's UI).

    Never stored - always derived from the private key on demand.
    """
    private_key = _load_key_object(load_private_key_pem())
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def validate_private_key(pem: str) -> None:
    """Reject anything that is not an EC P-256 private key, with friendly errors.

    Raises:
        AppleKeyError: with a message suitable for direct display in the UI.
    """
    if not isinstance(pem, str) or "PRIVATE KEY" not in pem:
        raise AppleKeyError(
            "That does not look like a private key in PEM format "
            "(expected a block starting with -----BEGIN PRIVATE KEY-----)."
        )
    key = _load_key_object(pem)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise AppleKeyError(
            "Apple Ads requires an elliptic-curve (EC) key; this looks like "
            f"a {type(key).__name__.replace('_', ' ')}. Generate a new key "
            "pair instead."
        )
    if not isinstance(key.curve, ec.SECP256R1):
        raise AppleKeyError(
            "Apple Ads requires the P-256 (prime256v1) curve; this key uses "
            f"{key.curve.name}. Generate a new key pair instead."
        )


def _load_key_object(pem: str):
    try:
        return serialization.load_pem_private_key(
            pem.encode("ascii"), password=None
        )
    except (ValueError, TypeError, UnicodeEncodeError) as e:
        raise AppleKeyError(
            "Could not read the private key - it may be corrupted, "
            "password-protected, or not PEM-encoded."
        ) from e
