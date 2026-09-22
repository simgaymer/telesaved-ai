# Roadmap

Completed bundle roadmap:

| Version | Bundle |
|---|---|
| 4.0.0 | Platform Foundation |
| 4.1.0 | Source & Telegram Intelligence |
| 4.2.0 | Cleanup, Review, and Duplicate Intelligence |
| 4.3.0 | Library Experience & Power User UX |
| 4.4.0 | Local User Management, RBAC, and Source ACL |
| 4.4.1 | LDAP Identity Backend |
| 4.4.2 | Advanced Granular Search |
| 4.5.0 | Foundation + Classification Core |
| 4.5.1 | AI Librarian UI + Audio Intelligence 2.0 |
| 4.5.2 | P0 Smoke Test + Transcript Save Endpoint |
| 4.5.3 | P1 Stabilisation + Gap Fixes |
| 4.5.4 | High-Priority Security + Reliability Fixes |
| 4.5.5 | Medium-Priority Gap Fixes |
| 4.5.6 | Tier 1 Modular Refactor — Serializers extracted |
| 4.5.7 | Ship-blocker Security + UX Fixes |

Recommended next priorities:

1. FAISS disk persistence (checkpoint on shutdown + `faiss.write_index` on startup).
2. SSO/OIDC integration.
3. CDN vendoring — bundle React/Babel/ReactDOM into `.deb` static assets (removes CDN dependency).
4. Media Health and Repair Center.
5. Performance optimization and scaling guidance (resource requirements per library size).
6. Admin audit log.
7. Backup/restore 2.0 — automated schedule + restore procedure.
8. SQLite write queue abstraction (future Postgres migration path).
9. Per-segment transcript undo (↺ icon in Audio Intelligence).
10. `⌘K` keyboard shortcut for AI Librarian.
