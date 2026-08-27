# User Management and RBAC

TeleSaved supports local users, groups, permissions, source access grants, and LDAP login. The initial Admin account is created through the browser-based setup wizard on first launch.

## Built-in groups

| Group | Rights |
|---|---|
| Admin | Full system access |
| Manager | Add/delete content, manage sources, grant source access, assign/disassign tags, run cleanup/duplicate jobs |
| Users | View-only library access; no Administration access |

Additional groups can be created with custom permission sets as needed.

## `tags.manage` permission

Admin and Manager groups have `tags.manage` by default. This grants:

- Access to the Tag Management panel (vocabulary management, suggestion queue, display mappings).
- Ability to submit inline tag suggestions from Review Studio and item detail pages.

Any custom group can be granted or revoked `tags.manage` independently.

## Source access control

Sources can be granted to individual users or groups. Backend checks are enforced on:

- Library results
- Video detail payloads
- Direct video URLs
- Thumbnails
- Previews
- Scene images

## LDAP

LDAP support includes server URI, StartTLS, bind DN/password, user search base/filter, group search base/filter, attribute mapping, default groups, and LDAP group to TeleSaved group mappings.

Keep one local emergency Admin account enabled even when LDAP is active. If the identity backend becomes unavailable, local login remains accessible.
