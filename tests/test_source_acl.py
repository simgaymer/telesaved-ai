"""Characterization tests for per-source access control (app.py source ACL)."""

import os
import shutil

import pytest


@pytest.fixture
def media(app_module, file_meta):
    """media(rel_path, source_ref=None) -> creates a real file under the download dir."""
    created = []
    root = app_module.get_download_directory()
    assert os.path.realpath(root).startswith(os.path.realpath(app_module.BASE_PATH))

    def _make(rel_path, source_ref=None):
        abs_path = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as f:
            f.write(b"\x00fake-video")
        created.append(abs_path)
        if source_ref is not None:
            file_meta(rel_path, source_ref=source_ref)
        return rel_path

    yield _make
    for abs_path in created:
        try:
            os.remove(abs_path)
        except FileNotFoundError:
            pass
    for sub in ("srcA", "srcB"):
        shutil.rmtree(os.path.join(root, sub), ignore_errors=True)


def _user(app_module, token):
    return app_module._auth.auth_current_user(_mock(token))


def _mock(token):
    from aiohttp.test_utils import make_mocked_request
    return make_mocked_request("GET", "/files/x", headers={"X-Telesaved-Auth-Token": token})


def _grant_user(app_module, username, refs):
    app_module.auth_data["source_access"]["users"][username] = list(refs)


def _grant_group(app_module, group, refs):
    app_module.auth_data["source_access"]["groups"][group] = list(refs)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_source_ref_for_path_legacy_maps_to_me(app_module, file_meta):
    assert app_module.source_ref_for_path("never/indexed.mp4") == "me"
    file_meta("blank.mp4", source_ref="")
    assert app_module.source_ref_for_path("blank.mp4") == "me"
    file_meta("saved.mp4", source_ref="Saved Messages")
    assert app_module.source_ref_for_path("saved.mp4") == "me"
    file_meta("link.mp4", source_ref="https://t.me/chanA")
    assert app_module.source_ref_for_path("link.mp4") == "@chanA"


@pytest.mark.parametrize("name, expected", [
    ("srcB__clip.mp4.jpg", os.path.join("srcB", "clip.mp4")),
    ("srcB__clip.mp4.gif", os.path.join("srcB", "clip.mp4")),
    ("srcB__clip.mp4__scene_3.jpg", os.path.join("srcB", "clip.mp4")),
    ("", ""),
])
def test_rel_path_from_derived_name(app_module, name, expected):
    assert app_module.rel_path_from_derived_name(name) == expected


def test_everything_open_before_bootstrap(app_module, fresh_auth):
    assert app_module.auth_initialized() is False
    assert app_module.user_can_access_source(None, "@anything") is True


def test_no_user_denied_after_bootstrap(app_module, make_user):
    make_user("Admin")
    assert app_module.user_can_access_source(None, "me") is False


def test_viewer_default_has_no_grants(app_module, make_user):
    viewer = _user(app_module, make_user("Users"))
    assert app_module.accessible_source_refs_for_user(viewer) == []
    assert app_module.user_can_access_source(viewer, "me") is False
    assert app_module.user_can_access_path(viewer, "legacy.mp4") is False


def test_user_grant(app_module, make_user, file_meta):
    viewer = _user(app_module, make_user("Users", username="vic"))
    _grant_user(app_module, "vic", ["@srcA"])
    file_meta("a.mp4", source_ref="@srcA")
    file_meta("b.mp4", source_ref="@srcB")
    assert app_module.accessible_source_refs_for_user(viewer) == ["@srcA"]
    assert app_module.user_can_access_path(viewer, "a.mp4") is True
    assert app_module.user_can_access_path(viewer, "b.mp4") is False


def test_group_grant_and_union(app_module, make_user):
    viewer = _user(app_module, make_user("Users", username="vic"))
    _grant_group(app_module, "Users", ["@srcB"])
    assert app_module.accessible_source_refs_for_user(viewer) == ["@srcB"]
    _grant_user(app_module, "vic", ["@srcA", "@srcB"])
    assert app_module.accessible_source_refs_for_user(viewer) == ["@srcA", "@srcB"]
    assert app_module.user_can_access_source(viewer, "@srcA") is True
    assert app_module.user_can_access_source(viewer, "@srcC") is False


def test_grants_are_normalized(app_module, make_user, file_meta):
    viewer = _user(app_module, make_user("Users", username="vic"))
    _grant_user(app_module, "vic", ["https://t.me/srcA", "saved"])
    file_meta("a.mp4", source_ref="@srcA")
    assert app_module.accessible_source_refs_for_user(viewer) == ["@srcA", "me"]
    assert app_module.user_can_access_path(viewer, "a.mp4") is True
    assert app_module.user_can_access_path(viewer, "legacy-no-meta.mp4") is True


@pytest.mark.parametrize("via", ["user", "group"])
def test_star_grant_opens_everything(app_module, make_user, via):
    viewer = _user(app_module, make_user("Users", username="vic"))
    if via == "user":
        _grant_user(app_module, "vic", ["*"])
    else:
        _grant_group(app_module, "Users", ["*"])
    assert app_module.accessible_source_refs_for_user(viewer) == ["*"]
    assert app_module.user_can_access_source(viewer, "@whatever") is True


def test_sources_manage_overrides_grants(app_module, make_user):
    manager = _user(app_module, make_user("Manager"))
    _grant_group(app_module, "Manager", [])
    assert app_module.accessible_source_refs_for_user(manager) == ["*"]
    assert app_module.user_can_access_source(manager, "@srcB") is True


def test_admin_star_overrides_grants(app_module, make_user):
    admin = _user(app_module, make_user("Admin"))
    _grant_group(app_module, "Admin", [])
    assert app_module.accessible_source_refs_for_user(admin) == ["*"]
    assert app_module.user_can_access_source(admin, "@srcB") is True


def test_system_admin_without_sources_manage(app_module, make_user):
    # A custom group with system.admin can open any source, but its
    # accessible_source_refs_for_user list still only reflects real grants.
    app_module.auth_data["groups"]["Ops"] = {"name": "Ops", "system": False, "permissions": ["system.admin"]}
    _grant_group(app_module, "Ops", [])
    ops = _user(app_module, make_user("Ops"))
    assert app_module.accessible_source_refs_for_user(ops) == []
    assert app_module.user_can_access_source(ops, "@srcB") is True


# ── HTTP: /files/, /thumbs/, /previews/ ───────────────────────────────────────

async def test_files_acl_over_http(client, make_user, headers, media, app_module):
    token = make_user("Users", username="vic")
    _grant_user(app_module, "vic", ["@srcA"])
    a = media("srcA/clip.mp4", "@srcA")
    b = media("srcB/clip.mp4", "@srcB")
    legacy = media("legacy.mp4")

    ok = await client.get(f"/files/{a}", headers=headers(token))
    assert ok.status == 200
    assert await ok.read() == b"\x00fake-video"

    denied = await client.get(f"/files/{b}", headers=headers(token))
    assert denied.status == 403
    body = await denied.json()
    assert body["forbidden"] is True and body["error"] == "You do not have access to this source"

    assert (await client.get(f"/files/{legacy}", headers=headers(token))).status == 403
    _grant_user(app_module, "vic", ["@srcA", "me"])
    assert (await client.get(f"/files/{legacy}", headers=headers(token))).status == 200


@pytest.mark.parametrize("prefix, suffix", [("/thumbs/", ".jpg"), ("/previews/", ".gif"), ("/scenes/", "__scene_1.jpg")])
async def test_derived_assets_acl(client, make_user, headers, file_meta, app_module, prefix, suffix):
    token = make_user("Users", username="vic")
    _grant_user(app_module, "vic", ["@srcA"])
    file_meta(os.path.join("srcA", "clip.mp4"), source_ref="@srcA")
    file_meta(os.path.join("srcB", "clip.mp4"), source_ref="@srcB")
    allowed = await client.get(f"{prefix}srcA__clip.mp4{suffix}", headers=headers(token))
    assert allowed.status == 404  # passed the ACL; no such asset on disk
    denied = await client.get(f"{prefix}srcB__clip.mp4{suffix}", headers=headers(token))
    assert denied.status == 403


@pytest.mark.parametrize("group", ["Manager", "Admin"])
async def test_privileged_groups_read_any_source(client, make_user, headers, media, app_module, group):
    token = make_user(group)
    _grant_group(app_module, group, [])
    b = media("srcB/clip.mp4", "@srcB")
    assert (await client.get(f"/files/{b}", headers=headers(token))).status == 200


async def test_files_require_library_view(client, make_user, headers, media, app_module):
    app_module.auth_data["groups"]["NoView"] = {"name": "NoView", "system": False, "permissions": ["downloads.view"]}
    token = make_user("NoView")
    _grant_group(app_module, "NoView", ["*"])
    a = media("srcA/clip.mp4", "@srcA")
    resp = await client.get(f"/files/{a}", headers=headers(token))
    assert resp.status == 403
    assert (await resp.json())["error"] == "Permission required:library.view"


async def test_grant_via_source_access_route(client, make_user, headers, media, app_module):
    viewer = make_user("Users", username="vic")
    manager = make_user("Manager")
    b = media("srcB/clip.mp4", "@srcB")
    assert (await client.get(f"/files/{b}", headers=headers(viewer))).status == 403

    resp = await client.post("/api/auth/source-access", headers=headers(manager),
                             json={"target_type": "group", "target": "Users", "source_refs": ["@srcB"]})
    assert resp.status == 200
    assert (await client.get(f"/files/{b}", headers=headers(viewer))).status == 200

    status = await (await client.get("/api/auth/status", headers=headers(viewer))).json()
    assert status["source_grants"] == ["@srcB"]


async def test_viewer_cannot_change_source_access(client, make_user, headers):
    viewer = make_user("Users", username="vic")
    resp = await client.post("/api/auth/source-access", headers=headers(viewer),
                             json={"target_type": "user", "target": "vic", "source_refs": ["*"]})
    assert resp.status == 403
