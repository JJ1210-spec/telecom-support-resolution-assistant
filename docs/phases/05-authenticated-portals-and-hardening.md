# Phase 5 Authenticated Customer and Admin Portals

## Completed and why

Added a customer sign-in/registration page and a separate administrator page. Customer accounts self-register with only the customer role; a local CLI creates admin accounts with a password prompt. Every protected API validates the session and role server-side, including direct access to an admin URL. Customer ticket reads and feedback additionally require ownership. Mutations require a per-session CSRF token. Passwords are salted with scrypt, sessions use random opaque tokens stored as hashes, and repeated bad passwords temporarily lock an account. The internal Knowledge and Assist analysis APIs use a separate random local service token to prevent bypass through direct URLs.

The portal persists incoming complaints, status, public and internal notes, customer feedback, events, and each AI analysis attempt. It saves a ticket before calling Ollama; if analysis fails, the ticket remains open for admin review and retry. Customer complaints have common email addresses and long numbers masked before AI/search, while the original local ticket is retained for support. The admin dashboard shows full source evidence and model timings, recent unknown-intent/abstention rates, service readiness, and a way to add or revise taxonomy classes.

Customer self-help is restricted to P3/P4 analyses. The model proposes candidate steps, but the displayed wording comes from a matching check line in a published KB article with sufficient similarity; titles, generic summaries, escalation lines, unsupported promises and operational actions are excluded. This is a conservative support check, not proof of semantic entailment.

## Verification

Automated tests cover login and role separation, CSRF enforcement, cross-customer ticket isolation, admin detail and status access, feedback, password hashing, PII masking before search, ticket persistence during model failure, severity gating, and rejection of article-title-only support. The existing retrieval and Assist tests still pass. Seventeen tests and Ruff lint passed at the Phase 5 checkpoint, with the previously logged Starlette test-client deprecation warning.

A real local Wi-Fi ticket first exposed a P2 over-classification, an unsupported duration in the draft summary, and no customer steps. A second live run after prompt/rule changes took 55.8 seconds, produced P3, and returned a model draft. The first candidate customer step matched only the KB title, so the final gate now ignores titles and maps only to actionable KB check lines. Applying the final gate to that saved real analysis yielded two specific KB checks: comparing Wi-Fi with a wired connection and checking placement, walls and band selection. The direct admin CLI import was also tested after resolving a circular import.

## Remaining limitations

The local account system is suitable for a localhost prototype, not a telecom deployment with real customer records. There is no HTTPS deployment, MFA, password reset, tenant isolation, comprehensive PII redaction, paginated queue loading, or enterprise audit retention policy. KB-line matching can miss a correct paraphrase or match an unsupported detail, so a human-reviewed citation-support evaluation remains necessary. The synthetic evaluation set and global score threshold do not establish customer safety or production accuracy. The model runtime can still fail; the guaranteed behavior is that a ticket is retained for human review, not that AI advice always appears. SQLite vector scanning is unchanged for this small corpus; a vector index is needed at larger scale. The drift signal is a simple rate alert, not automated drift detection.
