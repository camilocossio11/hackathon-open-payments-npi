import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from open_payments_sdk.gnap_utils.keys import KeyManager

KEYS_STORE_PATH = "keys_store.json"


def _load_store() -> dict:
    if os.path.exists(KEYS_STORE_PATH):
        with open(KEYS_STORE_PATH) as f:
            return json.load(f)
    return {}


def _save_store(store: dict) -> None:
    with open(KEYS_STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)


def get_key_for_wallet(wallet_address: str) -> dict | None:
    return _load_store().get(wallet_address)


def generate_keys(wallet_address: str) -> dict:
    km = KeyManager()
    key_pair = km.generate_key_pair()

    jwk = key_pair.jwks.keys[0]

    private_key = serialization.load_pem_private_key(
        key_pair.private_key_pem.encode(), password=None
    )
    public_key_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    store = _load_store()
    store[wallet_address] = {
        "kid": jwk.kid,
        "private_key_pem": key_pair.private_key_pem,
    }
    _save_store(store)

    public_key_b64 = base64.b64encode(json.dumps(jwk.__dict__).encode()).decode()

    return {
        "kid": jwk.kid,
        "public_key_b64": public_key_b64,
        "public_key_pem": public_key_pem,
        "jwks": jwk.__dict__,
    }
