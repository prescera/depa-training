import base64
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ISSUER = "https://kms.dc.example.org/app/oidc"
AUDIENCE = "api://depa-federation-broker/bankadata"
NOW = 1_800_000_000


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self.key = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {
            "kty": "EC", "crv": "P-256", "kid": self.kid, "use": "sig", "alg": "ES256",
            "x": b64url(n.x.to_bytes(32, "big")), "y": b64url(n.y.to_bytes(32, "big")),
        }  # fmt: skip

    def token(self, claims: dict | None = None, header: dict | None = None, **overrides) -> str:
        body = {
            "iss": ISSUER, "aud": AUDIENCE, "sub": "dataset:bank-a/credit-applications",
            "iat": NOW - 10, "nbf": NOW - 10, "exp": NOW + 290, "jti": "j-1",
            "contract_id": "c-1", "run_id": "r-1", "platform": "aci-snp", "measurement_id": "m",
        }  # fmt: skip
        body.update(claims or {})
        for k, v in overrides.items():
            if v is None:
                body.pop(k, None)
            else:
                body[k] = v
        hdr = {"alg": "ES256", "kid": self.kid, "typ": "JWT"} | (header or {})
        signing_input = f"{b64url(json.dumps(hdr).encode())}.{b64url(json.dumps(body).encode())}"
        r, s = decode_dss_signature(self.key.sign(signing_input.encode(), ec.ECDSA(SHA256())))
        return f"{signing_input}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


@pytest.fixture
def signer() -> Signer:
    return Signer()
