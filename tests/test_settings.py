"""Control page: token sign/verify + profile/settings service ops."""

from mealsentry.token import make_token, verify_token


def test_token_roundtrip_and_tamper():
    s = b"k" * 32
    tok = make_token(s, ttl=60)
    assert verify_token(s, tok) is True
    assert verify_token(b"other-secret", tok) is False   # wrong secret
    assert verify_token(s, "garbage") is False
    assert verify_token(s, None) is False


def test_token_expiry():
    s = b"k" * 32
    assert verify_token(s, make_token(s, ttl=-1)) is False  # already expired


async def test_dashboard_secret_stable_and_token(service):
    secret = await service.dashboard_secret()
    assert len(secret) == 32
    assert await service.dashboard_secret() == secret       # persisted, stable
    assert verify_token(secret, await service.make_dashboard_token()) is True


async def test_update_profile(service):
    p = await service.update_profile(name="Δοκιμή", weight_kg=90.0, age=40, steps_target=9000)
    assert p["name"] == "Δοκιμή" and p["weight_kg"] == 90.0 and p["age"] == 40
    assert p["steps_target"] == 9000
    # ignores unknown/None fields
    p2 = await service.update_profile(bogus="x", name=None)
    assert p2["name"] == "Δοκιμή"
