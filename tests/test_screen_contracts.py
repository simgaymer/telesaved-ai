"""Pin the response keys each SPA screen reads, so a backend/frontend rename fails here
instead of rendering an empty screen. Mirrors the adapters in frontend/index.html."""

import pytest

# (path, dotted keys the screen reads). "a.b" means body["a"]["b"].
SCREEN_CONTRACTS = [
    # AdminDashboard: library_stats.*, counts.*, jobs.active, storage.*. Regression: NameError on `device`.
    ("/api/admin/overview", ["library_stats.total_videos", "library_stats.total_size_human",
                             "library_stats.by_source", "counts.indexed_files", "counts.review_queue",
                             "jobs.active", "storage.used_human", "storage.percent_used", "system.device"]),
    ("/api/jobs", ["jobs.active", "jobs.recent"]),                         # JobsPage
    ("/api/watch", ["watch_later"]),                                       # WatchLaterPage
    ("/api/admin/review/studio", ["items"]),                               # AdminReviewStudio
    ("/api/auth/admin", ["users", "groups"]),                              # AdminUsers (no GET /api/auth/users)
    ("/api/sources", ["sources", "profiles", "dialogs"]),                  # AdminSources
    ("/api/admin/scoring", ["weights", "thresholds", "bias", "tags", "nudenet_threshold"]),  # AdminScoring
    ("/api/admin/system", ["auto_refresh", "download_directory", "video_sources"]),           # AdminSystem
    ("/api/admin/logs", ["lines"]),                                        # AdminLogs
    ("/api/admin/feedback", ["history"]),                                  # AdminLogs → Feedback
    ("/api/admin/audit-log", ["entries"]),                                 # AdminLogs → Audit
    ("/api/tags/mappings", ["mappings"]),                                  # AdminTagManagement
    ("/api/tags/suggestions", ["suggestions"]),
    ("/api/collections", ["collections", "counts"]),                       # CollectionsPage
    ("/api/trash", ["items"]),                                             # TrashPage
    ("/api/downloads", ["active", "history", "storage"]),                  # DownloadsPage
    ("/api/personal/library", ["items"]),                                  # PersonalPage
    ("/api/library", ["items", "total_items", "total_pages"]),             # LibraryPage
]


def _dig(body, dotted):
    cur = body
    for part in dotted.split("."):
        assert isinstance(cur, dict) and part in cur, f"missing key {dotted!r}"
        cur = cur[part]
    return cur


@pytest.mark.parametrize("path,keys", SCREEN_CONTRACTS, ids=[p for p, _ in SCREEN_CONTRACTS])
async def test_screen_payload_contract(client, make_user, headers, path, keys):
    token = make_user("Admin")
    resp = await client.get(path, headers=headers(token))
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    for key in keys:
        _dig(body, key)


async def test_jobs_summary_lists_are_lists(client, make_user, headers):
    body = await (await client.get("/api/jobs", headers=headers(make_user("Admin")))).json()
    assert isinstance(body["jobs"]["active"], list) and isinstance(body["jobs"]["recent"], list)
