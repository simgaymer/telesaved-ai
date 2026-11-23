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
| 4.5.8 | Tier 2 Modular Refactor + Backlog Sweep |
| 4.5.9 | FAISS disk persistence + reliability fixes |
| 4.5.10 | Tier 2 Modular Refactor: state.py extracted |
| 4.5.11 | Admin audit log |
| 4.5.12 | Tier 2 Modular Refactor: pipeline.py extracted |
| 4.5.15 | Tier 2 Modular Refactor: COMPLETE (auth.py, jobs.py, sources.py extracted) |

Recommended next priorities:

1. SSO/OIDC integration.
2. CDN vendoring — bundle React/Babel/ReactDOM into `.deb` static assets (removes CDN dependency).
3. Media Health and Repair Center.
4. Performance optimization and scaling guidance (resource requirements per library size).
5. Backup/restore 2.0 — automated schedule + restore procedure.
6. SQLite write queue abstraction (future Postgres migration path).
7. Per-segment transcript undo (↺ icon in Audio Intelligence).
8. `⌘K` keyboard shortcut for AI Librarian.
