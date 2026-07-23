# User Management and RBAC

TeleSaved supports local users, groups, permissions, source access grants, and LDAP login.

## Built-in groups

| Group | Rights |
|---|---|
| Admin | Full system access |
| Manager | Add/delete content, manage sources, grant source access, assign/disassign tags, run cleanup/duplicate jobs |
| Users | View-only library access; no Administration access |

Additional groups can be created as needed.

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

Keep one local emergency Admin account enabled even when LDAP is active.
