"""Route coverage: every registered route must have an explicit, reviewed permission.

``required_permission_for_request`` falls back to ``library.view`` for any /api/
path it does not recognise, so a new admin-only route would silently ship open
to every logged-in user. These tests make adding a route a conscious decision:

* a route registered in app.py but missing from EXPECTED fails
  ``test_every_api_route_is_listed`` with the method and path to add;
* a listed route whose permission changes fails its own parametrized row.

When you add a route, add it here with the permission it SHOULD require. If the
route map gives it something else, fix ``required_permission_for_request``.
``None`` means the middleware asks for no permission (the handler checks itself,
or the path is public).
"""

import re

import pytest
from aiohttp.test_utils import make_mocked_request

# Middleware lets these through without any login (app.py auth_middleware).
PUBLIC_API = {
    "/api/health", "/api/setup/status", "/api/setup/validate-telegram",
    "/api/auth/status", "/api/auth/login", "/api/auth/bootstrap",
    "/api/auth/oidc/login", "/api/auth/oidc/callback",
}

# Non-API routes: static media (source ACL enforced by each handler) and the SPA shell.
EXPECTED_NON_API = {
    ("GET", "/"),
    ("GET", "/files/{path:.*}"),
    ("GET", "/frame-preview/{video_key}/{frame_name}"),
    ("GET", "/previews/{name:.*}"),
    ("GET", "/scenes/{name:.*}"),
    ("GET", "/thumbs/{name:.*}"),
    ("GET", "/{tail:.*}"),
}

EXPECTED = {
    # admin
    ('GET', '/api/admin/audit-log'): 'system.admin',
    ('GET', '/api/admin/backups'): 'system.admin',
    ('POST', '/api/admin/backups'): 'system.admin',
    ('POST', '/api/admin/backups/restore'): 'system.admin',
    ('GET', '/api/admin/backups/{filename}'): 'system.admin',
    ('POST', '/api/admin/cleanup/action'): 'system.admin',
    ('GET', '/api/admin/cleanup/plan'): 'system.admin',
    ('GET', '/api/admin/cleanup/studio'): 'system.admin',
    ('GET', '/api/admin/database'): 'system.admin',
    ('GET', '/api/admin/duplicates'): 'system.admin',
    ('GET', '/api/admin/duplicates/advanced'): 'system.admin',
    ('POST', '/api/admin/duplicates/delete'): 'system.admin',
    ('POST', '/api/admin/duplicates/delete-exact'): 'system.admin',
    ('POST', '/api/admin/duplicates/scan'): 'system.admin',
    ('GET', '/api/admin/feedback'): 'system.admin',
    ('GET', '/api/admin/health'): 'system.admin',
    ('GET', '/api/admin/index-sweep'): 'system.admin',
    ('POST', '/api/admin/index-sweep'): 'system.admin',
    ('GET', '/api/admin/library-map'): 'system.admin',
    ('GET', '/api/admin/logs'): 'system.admin',
    ('POST', '/api/admin/migrate-library'): 'system.admin',
    ('GET', '/api/admin/overview'): 'system.admin',
    ('POST', '/api/admin/reprocess-all'): 'system.admin',
    ('POST', '/api/admin/review/batch'): 'system.admin',
    ('GET', '/api/admin/review/studio'): 'system.admin',
    ('GET', '/api/admin/scoring'): 'system.admin',
    ('POST', '/api/admin/scoring'): 'system.admin',
    ('POST', '/api/admin/scoring/reset'): 'system.admin',
    ('GET', '/api/admin/source-intelligence'): 'system.admin',
    ('GET', '/api/admin/source-pipelines'): 'system.admin',
    ('POST', '/api/admin/source-pipelines/run'): 'system.admin',
    ('GET', '/api/admin/stats'): 'system.admin',
    ('GET', '/api/admin/system'): 'system.admin',
    ('POST', '/api/admin/system'): 'system.admin',
    ('GET', '/api/admin/telegram-metadata'): 'system.admin',
    ('POST', '/api/admin/telegram-metadata/backfill'): 'system.admin',
    ('GET', '/api/admin/visual-clusters'): 'system.admin',
    ('POST', '/api/admin/visual-clusters/scan'): 'system.admin',
    # audio
    ('POST', '/api/audio/analyze-missing'): 'admin.view',
    ('GET', '/api/audio/overview'): 'admin.view',
    ('GET', '/api/audio/search'): 'admin.view',
    # auth
    ('GET', '/api/auth/admin'): None,
    ('POST', '/api/auth/bootstrap'): None,
    ('POST', '/api/auth/groups'): None,
    ('DELETE', '/api/auth/groups/{group_name}'): None,
    ('PUT', '/api/auth/groups/{group_name}'): None,
    ('GET', '/api/auth/ldap/settings'): None,
    ('PUT', '/api/auth/ldap/settings'): None,
    ('POST', '/api/auth/ldap/sync-user'): None,
    ('POST', '/api/auth/ldap/test'): None,
    ('POST', '/api/auth/login'): None,
    ('POST', '/api/auth/logout'): None,
    # OIDC: login/callback are public; settings/test check security.manage in the handler.
    ('GET', '/api/auth/oidc/callback'): None,
    ('GET', '/api/auth/oidc/login'): None,
    ('GET', '/api/auth/oidc/settings'): None,
    ('PUT', '/api/auth/oidc/settings'): None,
    ('POST', '/api/auth/oidc/test'): None,
    ('POST', '/api/auth/source-access'): None,
    ('GET', '/api/auth/status'): None,
    ('POST', '/api/auth/users'): None,
    ('DELETE', '/api/auth/users/{user_id}'): None,
    ('PUT', '/api/auth/users/{user_id}'): None,
    # bulk
    ('POST', '/api/bulk/reprocess'): 'content.manage',
    ('POST', '/api/bulk/tags'): 'tags.manage',
    # collections
    ('GET', '/api/collections'): 'admin.view',
    ('POST', '/api/collections'): 'content.manage',
    ('DELETE', '/api/collections/{collection_id}'): 'content.manage',
    ('PUT', '/api/collections/{collection_id}'): 'content.manage',
    ('GET', '/api/collections/{collection_id}/items'): 'admin.view',
    # delete-bulk
    ('POST', '/api/delete-bulk'): 'content.delete',
    # delete-tag
    ('POST', '/api/delete-tag'): 'content.delete',
    # downloads
    ('GET', '/api/downloads'): 'library.view',
    ('POST', '/api/downloads/pause'): 'jobs.manage',
    ('POST', '/api/downloads/resume'): 'jobs.manage',
    # export
    ('GET', '/api/export/library'): 'system.admin',
    # health
    ('GET', '/api/health'): 'library.view',
    # import
    ('POST', '/api/import/library'): 'system.admin',
    # install
    ('GET', '/api/install/checks'): 'library.view',
    # item
    ('DELETE', '/api/item/{path:.*}'): 'content.delete',
    ('GET', '/api/item/{path:.*}'): 'library.view',
    ('GET', '/api/item/{path:.*}/audio'): 'library.view',
    ('POST', '/api/item/{path:.*}/audio/analyze'): 'content.manage',
    ('POST', '/api/item/{path:.*}/audio/transcript'): 'content.manage',
    ('POST', '/api/item/{path:.*}/notes'): 'content.manage',
    ('POST', '/api/item/{path:.*}/personal'): 'content.manage',
    ('POST', '/api/item/{path:.*}/playback'): 'content.manage',
    ('POST', '/api/item/{path:.*}/rename'): 'content.manage',
    ('POST', '/api/item/{path:.*}/reprocess'): 'content.manage',
    ('GET', '/api/item/{path:.*}/scenes'): 'library.view',
    ('POST', '/api/item/{path:.*}/scenes'): 'content.manage',
    ('GET', '/api/item/{path:.*}/similar'): 'library.view',
    ('POST', '/api/item/{path:.*}/tags'): 'tags.manage',
    ('POST', '/api/item/{path:.*}/tags/clear'): 'tags.manage',
    ('GET', '/api/item/{path:.*}/transcript.srt'): 'library.view',
    ('GET', '/api/item/{path:.*}/transcript.vtt'): 'library.view',
    ('POST', '/api/item/{path:.*}/watch-later'): 'content.manage',
    ('POST', '/api/item/{path:.*}/watched'): 'content.manage',
    # jobs
    ('GET', '/api/jobs'): 'jobs.manage',
    ('POST', '/api/jobs/enqueue'): 'jobs.manage',
    ('POST', '/api/jobs/{job_id}/cancel'): 'jobs.manage',
    ('POST', '/api/jobs/{job_id}/pause'): 'jobs.manage',
    ('POST', '/api/jobs/{job_id}/resume'): 'jobs.manage',
    # librarian
    ('POST', '/api/librarian/ask'): 'library.view',
    ('GET', '/api/librarian/suggestions'): 'library.view',
    # library
    ('GET', '/api/library'): 'library.view',
    # personal
    ('GET', '/api/personal/library'): 'admin.view',
    # privacy
    ('POST', '/api/privacy/lock'): 'system.admin',
    ('GET', '/api/privacy/settings'): 'system.admin',
    ('POST', '/api/privacy/settings'): 'system.admin',
    ('GET', '/api/privacy/status'): 'library.view',
    ('POST', '/api/privacy/unlock'): 'system.admin',
    # review
    ('GET', '/api/review'): 'admin.view',
    ('POST', '/api/review/{path:.*}/accept'): 'tags.manage',
    ('POST', '/api/review/{path:.*}/skip'): 'tags.manage',
    ('POST', '/api/review/{path:.*}/unskip'): 'tags.manage',
    # rules
    ('GET', '/api/rules'): 'content.manage',
    ('POST', '/api/rules'): 'content.manage',
    ('POST', '/api/rules/run'): 'content.manage',
    ('DELETE', '/api/rules/{rule_id}'): 'content.manage',
    ('PUT', '/api/rules/{rule_id}'): 'content.manage',
    ('POST', '/api/rules/{rule_id}/run'): 'content.manage',
    # setup
    ('POST', '/api/setup/save'): 'system.admin',
    ('GET', '/api/setup/status'): 'library.view',
    ('POST', '/api/setup/validate-telegram'): 'library.view',
    # sources
    ('GET', '/api/sources'): 'sources.manage',
    ('POST', '/api/sources'): 'sources.manage',
    # storage
    ('GET', '/api/storage'): 'library.view',
    # tags
    ('GET', '/api/tags'): 'library.view',
    ('POST', '/api/tags'): 'tags.manage',
    ('GET', '/api/tags/labels/export'): 'system.admin',
    ('GET', '/api/tags/mappings'): 'library.view',
    ('POST', '/api/tags/mappings'): 'tags.manage',
    ('POST', '/api/tags/metadata'): 'tags.manage',
    ('POST', '/api/tags/migrate-labels'): 'system.admin',
    ('POST', '/api/tags/suggest'): 'tags.manage',
    ('GET', '/api/tags/suggestions'): 'system.admin',
    ('POST', '/api/tags/suggestions/{suggestion_id}/approve'): 'tags.manage',
    ('POST', '/api/tags/suggestions/{suggestion_id}/reject'): 'tags.manage',
    ('DELETE', '/api/tags/{tag}'): 'tags.manage',
    # trash
    ('GET', '/api/trash'): 'content.delete',
    ('POST', '/api/trash/empty'): 'content.delete',
    ('DELETE', '/api/trash/{trash_id}'): 'content.delete',
    ('POST', '/api/trash/{trash_id}/restore'): 'content.delete',
    # watch
    ('GET', '/api/watch'): 'admin.view',
}


def _concrete(path):
    return re.sub(r"\{[^}:]+(:[^}]*)?\}", "x", path)


def _registered(app_module):
    return {(r.method, r.path) for r in app_module.routes}


def test_every_api_route_is_listed(app_module):
    api = {key for key in _registered(app_module) if key[1].startswith("/api/")}
    missing = sorted(api - set(EXPECTED))
    stale = sorted(set(EXPECTED) - api)
    assert not missing, f"Routes with no reviewed permission in tests/test_route_coverage.py: {missing}"
    assert not stale, f"EXPECTED lists routes app.py no longer registers: {stale}"


def test_non_api_routes_are_known(app_module):
    other = {key for key in _registered(app_module) if not key[1].startswith("/api/")}
    assert other == EXPECTED_NON_API


def test_public_paths_match_middleware(app_module):
    import inspect
    src = inspect.getsource(app_module.auth_middleware)
    for path in PUBLIC_API:
        assert f'"{path}"' in src, f"{path} is no longer public in auth_middleware"


@pytest.mark.parametrize("method,path", sorted(EXPECTED), ids=lambda v: v)
def test_route_permission(app_module, method, path):
    request = make_mocked_request(method, _concrete(path))
    assert app_module.required_permission_for_request(request) == EXPECTED[(method, path)]


async def test_every_route_is_reachable(app_module):
    """aiohttp matches routes in registration order, so a catch-all such as
    GET /api/item/{path:.*} registered too early swallows /api/item/x/similar.
    Regression: 4.5.x shipped with transcript.srt/.vtt, /similar and /audio unreachable."""
    from conftest import build_web_app
    web_app = build_web_app(app_module)
    web_app.freeze()
    wrong = []
    for route in app_module.routes:
        path = route.path
        if path in ("/{tail:.*}",):
            continue
        concrete = re.sub(r"\{[^}:]+(:[^}]*)?\}", "x", path)
        match = await web_app.router.resolve(make_mocked_request(route.method, concrete))
        if getattr(match, "handler", None) is not route.handler:
            wrong.append((route.method, path, getattr(match.handler, "__name__", match)))
    assert not wrong, f"Routes shadowed by an earlier registration: {wrong}"
