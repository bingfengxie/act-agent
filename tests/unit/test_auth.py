import jwt
import pytest

from act_agent.auth.jwt import create_access_token, decode_access_token, principal_from_claims
from act_agent.auth.passwords import hash_password, verify_password
from act_agent.settings import Settings


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password(hashed, "s3cret")
    assert not verify_password(hashed, "wrong")


def test_jwt_roundtrip_carries_tenant_and_scopes():
    settings = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
    token = create_access_token(
        user_id="u1", tenant_id="t1", username="alice", roles=["operator"], settings=settings
    )
    claims = decode_access_token(token, settings)
    principal = principal_from_claims(claims)
    assert principal.tenant_id == "t1"
    assert principal.user_id == "u1"
    assert principal.has("activity:write")
    assert not principal.has("activity:delete")


def test_tampered_token_rejected():
    settings = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
    token = create_access_token(
        user_id="u1", tenant_id="t1", username="alice", roles=["admin"], settings=settings
    )
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token + "x", settings)
