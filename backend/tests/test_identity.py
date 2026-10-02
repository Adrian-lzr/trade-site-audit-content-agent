from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from backend.authz import identity_from_request
from backend.identity import IdentityVerificationError, OIDCConfig, verify_bearer_token


JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "fixture",
            "n": "ygl6Sfv_UC7KtK3gODOOH3Q_qUQDfSc5DA2ONK2nivHI9kX72WT5-8pQOAb9nGBP3VEQxmXPB0fr_Rbx26Q5uthCT8kaIhJ9c4prcmBpATbu9DKBJi98Wrl1_not6z3ZEjbRSJMrWWZjcp8SlU26kh_kV1oNtMnDTLyuXuyebm2TOeldmhcEUOPyUmeRFBFH47s6ce-ybnEL3o90p_5QKUPtDmVr4FWpdSWRKnDhZ758Cd5F7icDVs8qAmGxOFFsdjms1991a4qaqHWGIu5Or0nqO6MLOAeCr0J0mqpbSZkbiiz9tIJWzGIpu2Ndlj5vofHsEiHyUXbdJdR6hQf-xw",
            "e": "AQAB",
        }
    ]
}
TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6ImZpeHR1cmUifQ.eyJpc3MiOiJodHRwczovL2lzc3Vlci5leGFtcGxlLnRlc3QiLCJhdWQiOiJzaXRlLWF1ZGl0Iiwic3ViIjoiYWxpY2UiLCJleHAiOjQxMDI0NDQ4MDB9.wUqjWdwgFyN6wbNm4vyjUfkbrAHIwVHK06MVTWtc4z1SdMUAkXoRNGH4HVzGmsnxkiuebJ0RJUCTRihqVbICDxn_aTuIjwFrZdGx9quP6ykqdbw3cNtkCfPzOXGtWGl-JLIcIYj2A9O01ce2VHloqs1LA6EX9R7e9o2lAlA5oTqwcDOz0Vvs6fqdwUygmeNW4Uj02-Tclm1p8_8WRkux0Rj1Qv-lWbUUDngigZFFCPol63homP8P0-_EGCHG-4plHvfT3IHm3PpFr_wYszJ4z2QrcXTXdU8m_uA8YkaQ-E6sA0x3p7WU3HocqXyQ-fSi4gf9f60IfkejR0gHw08sHA"


def config(**kwargs) -> OIDCConfig:
    values = {
        "mode": "production",
        "issuer": "https://issuer.example.test",
        "audience": "site-audit",
        "jwks_json": json.dumps(JWKS),
        "jwks_url": None,
        "allowed_algorithms": frozenset({"RS256"}),
    }
    values.update(kwargs)
    return OIDCConfig(**values)


def test_fixture_token_verifies_and_returns_trusted_subject():
    assert verify_bearer_token(TOKEN, config(), now=1_700_000_000) == "alice"


@pytest.mark.parametrize(
    "changes,now",
    [({"issuer": "https://wrong.example.test"}, 1_700_000_000), ({"audience": "other"}, 1_700_000_000), ({}, 4_200_000_000)],
)
def test_claim_policy_rejects_wrong_issuer_audience_or_expiry(changes, now):
    with pytest.raises(IdentityVerificationError):
        verify_bearer_token(TOKEN, config(**changes), now=now)


def test_production_authz_rejects_local_header_and_requires_bearer(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    with pytest.raises(HTTPException) as local:
        identity_from_request({"X-Local-User": "alice"})
    assert local.value.status_code == 401
    with pytest.raises(HTTPException) as missing:
        identity_from_request({})
    assert missing.value.status_code == 401


def test_bearer_identity_is_used_in_production(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "production")
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("OIDC_AUDIENCE", "site-audit")
    monkeypatch.setenv("OIDC_JWKS_JSON", json.dumps(JWKS))
    context = identity_from_request({"Authorization": f"Bearer {TOKEN}"})
    assert context.user_id == "alice"
    assert context.source == "oidc"
