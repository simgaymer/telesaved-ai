# User Management, RBAC, and Source ACL

TeleSaved supports local users, groups, permissions, source access controls, and LDAP integration. First-run admin account creation is handled by the browser-based setup wizard.

## Built-in groups

| Group | Purpose |
|---|---|
| Admin | Full system access |
| Manager | Add/delete content, manage sources, assign/disassign tags, grant source access |
| Users | View-only access; no Administration access |

Additional groups can be created with custom permission sets.

## Permission model

Permissions are enforced by the backend. Hiding buttons in the UI is not the only access-control layer.

Common permissions include:

- `admin.view`
- `library.view`
- `downloads.view`
- `content.manage`
- `content.delete`
- `tags.manage` — access to Tag Management panel; submit tag suggestions in Review Studio
- `sources.manage`
- `source_acl.manage`
- `jobs.manage`
- `security.manage`
- `system.admin`
- `*`

## `tags.manage` permission

Users with `tags.manage` can:

- Access the Tag Management panel (Admin and Manager groups have this by default).
- Submit new tag suggestions from the inline input in Review Studio and item detail pages.
- Custom groups can be granted or revoked `tags.manage` independently of their other permissions.

## Source access

Source access can be granted by user or by group. A user can see library items only from sources they are allowed to access unless they have administrative source privileges.

## Bootstrap

On first launch, the browser-based setup wizard creates the initial Admin account. Keep at least one local emergency Admin account active, especially when using LDAP.

## LDAP backend

LDAP support includes:

- Server URI
- StartTLS
- Bind DN and password
- User search base and filter
- Username, display name, and email attributes
- `memberOf` and group search support
- LDAP group to TeleSaved group mappings
- Auto-create/sync LDAP users

Run the dependency helper when enabling LDAP:

```bash
sudo /opt/telesaved/install-ldap-deps.sh
```
