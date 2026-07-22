# User Management, RBAC, and Source ACL

TeleSaved 4.4.x added local users, groups, permissions, source access controls, and LDAP integration.

## Built-in groups

| Group | Purpose |
|---|---|
| Admin | Full system access |
| Manager | Add/delete content, manage sources, assign/disassign tags, grant source access |
| Users | View-only access; no Administration access |

## Permission model

Permissions are enforced by the backend. Hiding buttons in the UI is not the only access-control layer.

Common permissions include:

- `admin.view`
- `library.view`
- `downloads.view`
- `content.manage`
- `content.delete`
- `tags.manage`
- `sources.manage`
- `source_acl.manage`
- `jobs.manage`
- `security.manage`
- `system.admin`
- `*`

## Source access

Source access can be granted by user or by group. A user can see library items only from sources they are allowed to access unless they have administrative source privileges.

## Bootstrap

On first launch with no users, TeleSaved prompts for the first local Admin account. Keep at least one local emergency Admin, especially when using LDAP.

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
