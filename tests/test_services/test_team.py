import pytest

from app.services.team import (
    accept_invite,
    invite_staff,
    list_staff,
    revoke_staff,
    update_permissions,
)


async def test_invite_requires_identity(db, shop):
    with pytest.raises(ValueError, match="phone or line_id"):
        await invite_staff(db, shop)


async def test_invite_defaults(db, shop):
    staff = await invite_staff(db, shop, phone="0899999999", display_name="Nok")
    assert staff.can_void is True         # default on
    assert staff.can_deereach is False    # default off
    assert staff.can_topup is False
    assert staff.can_settings is False
    assert staff.accepted_at is None      # pending until login


async def test_accept_invite(db, shop):
    staff = await invite_staff(db, shop, phone="0899999999")
    accepted = await accept_invite(db, staff)
    assert accepted.accepted_at is not None


async def test_update_permissions_valid_keys(db, shop):
    staff = await invite_staff(db, shop, phone="0899999999")
    await update_permissions(db, staff, can_deereach=True, can_topup=True)
    assert staff.can_deereach is True
    assert staff.can_topup is True


async def test_update_permissions_invalid_key_raises(db, shop):
    staff = await invite_staff(db, shop, phone="0899999999")
    with pytest.raises(ValueError, match="Invalid permission"):
        await update_permissions(db, staff, can_fly=True)


async def test_list_excludes_revoked_by_default(db, shop):
    a = await invite_staff(db, shop, phone="0811111111", display_name="Active")
    b = await invite_staff(db, shop, phone="0822222222", display_name="Revoked")
    await revoke_staff(db, b)

    active_only = await list_staff(db, shop.id)
    assert [s.display_name for s in active_only] == ["Active"]

    all_members = await list_staff(db, shop.id, include_revoked=True)
    assert {s.display_name for s in all_members} == {"Active", "Revoked"}
