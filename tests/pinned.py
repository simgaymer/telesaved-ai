"""Pinned expectations shared by several test modules.

Changing a default group's permissions in telesaved/auth.py must be a
deliberate act: update this table in the same change.
"""

EXPECTED_DEFAULT_GROUP_PERMISSIONS = {
    "Admin": ["*"],
    "Manager": [
        "admin.view", "library.view", "downloads.view", "content.manage", "content.delete",
        "tags.manage", "sources.manage", "source_acl.manage", "jobs.manage",
    ],
    "Users": ["library.view", "downloads.view"],
}
