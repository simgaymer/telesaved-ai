"""Review Studio: the per-item fields the screen renders, and that Accept / Edit Tags actually
take an item out of the queue. All paths are synthetic and never exist on disk."""

from urllib.parse import quote

import pytest

# Every key AdminReviewStudio reads from an item (frontend/index.html).
STUDIO_ITEM_KEYS = ["path", "filename", "tags", "score_map", "exists", "file_url", "thumb_url",
                    "display_tags", "size_human", "duration", "source", "review_reasons",
                    "review_category", "review_priority", "suggested_action"]


@pytest.fixture
def indexed(app_module):
    """indexed(rel_path, tags=..., rule_flag=None) adds a synthetic index entry; all traces removed after."""
    added = []

    def _add(rel_path, tags=("Clip",), rule_flag=None):
        app_module.tags[rel_path] = list(tags)
        if rule_flag:
            app_module.metadata_store[rel_path] = {
                "rule_review_flags": [{"rule_id": rule_flag, "rule_name": rule_flag, "flagged_at": "x"}]}
        added.append(rel_path)
        return rel_path

    yield _add
    for rel_path in added:
        app_module.tags.pop(rel_path, None)
        app_module.metadata_store.pop(rel_path, None)
        app_module.feedback_data.get("manual_overrides", {}).pop(rel_path, None)
        app_module.review_skip_map().pop(rel_path, None)


async def _studio(client, headers, token):
    resp = await client.get("/api/admin/review/studio", headers=headers(token))
    assert resp.status == 200, await resp.text()
    return await resp.json()


def _paths(body):
    return [item["path"] for item in body["items"]]


async def test_studio_item_contract(client, make_user, headers, indexed):
    path = indexed("zz-test/studio contract #1.mp4")
    body = await _studio(client, headers, make_user("Admin"))
    for key in ("count", "skipped_count", "categories"):
        assert key in body
    item = next(i for i in body["items"] if i["path"] == path)
    for key in STUDIO_ITEM_KEYS:
        assert key in item, f"missing item key {key!r}"
    assert "label" in item["review_priority"]
    # "#" must be escaped or the <video src> is cut at the fragment.
    assert item["file_url"] == "/files/" + quote(path)


async def test_accept_clears_rule_flag_from_queue(client, make_user, headers, indexed, app_module):
    token = make_user("Admin")
    path = indexed("zz-test/rule-flagged.mp4", rule_flag="needs-eyes")
    item = next(i for i in (await _studio(client, headers, token))["items"] if i["path"] == path)
    assert item["review_category"] == "Rule Flag"

    resp = await client.post(f"/api/review/{quote(path)}/accept", headers=headers(token))
    assert resp.status == 200, await resp.text()
    assert path not in _paths(await _studio(client, headers, token))
    meta = app_module.metadata_store[path]
    assert "rule_review_flags" not in meta
    assert meta["rule_review_resolved"][0]["rule_id"] == "needs-eyes"


async def test_tag_save_clears_rule_flag_from_queue(client, make_user, headers, indexed):
    token = make_user("Admin")
    path = indexed("zz-test/rule-flagged-edit.mp4", rule_flag="needs-eyes")
    resp = await client.post(f"/api/item/{quote(path)}/tags", json={"tags": ["Movie"]}, headers=headers(token))
    assert resp.status == 200, await resp.text()
    assert path not in _paths(await _studio(client, headers, token))


async def test_skip_hides_item_and_counts_it(client, make_user, headers, indexed):
    token = make_user("Admin")
    path = indexed("zz-test/skip-me.mp4")
    before = await _studio(client, headers, token)
    assert path in _paths(before)
    assert (await client.post(f"/api/review/{quote(path)}/skip", headers=headers(token))).status == 200
    after = await _studio(client, headers, token)
    assert path not in _paths(after)
    assert after["skipped_count"] == before["skipped_count"] + 1
