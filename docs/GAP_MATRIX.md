# V2 gap matrix against the 19 October 2026 v1 analysis

Status describes the shipped scope, not production acceptance. **Implemented** means code/policy exists with cited offline checks where applicable. **Partial** means a useful subset exists but the original issue or verification remains open. **Deferred** means no supported implementation/required real-world proof. See `TEST_REPORT.md` for final executed counts; test references below are not independent claims that live services ran.

| ID | Status | Implementation/evidence and tests | Remaining blocker or limitation |
| --- | --- | --- | --- |
| A1 | Implemented | `app/discovery.py`, source discovery API/UI; same-host local-card oncology candidate extraction and sitemap traversal; `tests/test_discovery.py` | Native hospital discovery/coverage unproved; candidates still need review |
| A2 | Partial | Hospital/source tables, approval metadata, source UI/API; `test_live_sources_are_approved_not_implicitly_trusted` | No approved NCG/major-chain master seeded; Max/Fortis references remain UNAPPROVED; branch/city mapping needs humans |
| A3 | Partial | Bounded `rel=next`, query/path pagination links; pagination and attempt-limit tests in `test_discovery.py` | JavaScript “load more”/rendering deferred; no real-site pagination trial |
| A4 | Implemented | `SOURCE_POLICY.md`: official institutional sites only, internal research; source classification persisted | Aggregators excluded; no claim of smaller-hospital/national coverage |
| B1 | Implemented | `SafeFetcher` treats robots 404 as no rules; `test_missing_robots_allows_and_is_cached` | Forbidden/unavailable/malformed robots still block; site permission separate |
| B2 | Partial | Bounded gzip/deflate source and robots responses; bomb/corruption/chunk tests in `test_fetcher.py` | Brotli unsupported; no native CDN run succeeded |
| B3 | Implemented | Identifying v2 UA plus optional validated HTTPS/mailto contact; config tests | Operator must supply a real authorized contact if desired; bot blocks can remain |
| B4 | Implemented | XHTML decoding; `test_xhtml_is_visible_html` | PDFs, images/OCR unsupported |
| B5 | Deferred | JS-only pages reported honestly by discovery coverage test | No Playwright/renderer; must preserve transport controls if added |
| B6 | Deferred | Pinned HTTPS transport preserved | No proxy support; no network/access-control workaround |
| B7 | Implemented | Database `host_pacing`, shared API/worker pacing; fetcher callback and larger-robots-delay tests | Real PostgreSQL multiprocess scale/traffic behavior untested; keep one coordinator initially |
| B8 | Implemented | Refresh generations/hash comparison/quarantine/reapproval; `test_directory_workflow.py` (including pre-extraction quarantine/model failure), refresh race/reversion tests in `test_independent_audit.py` | Manual only; no schedule; merged sources must be reconciled; real freshness trial absent |
| C1 | Partial | Broader leaf-label/heading parsing plus optional LLM path; extractor/LLM tests | Complete native hospital extraction accuracy unmeasured; reconstructed excerpts yield only supported name fallback |
| C2 | Partial | `LLMProvider`, OpenAI/Mistral fixed-endpoint adapters and mocked compatibility/usage tests | No paid/live calls, 10-page provider trial, model-account compatibility or measured correction rate |
| C3 | Implemented | 40,000-character default; bounded literal selection and nav/footer/cookie reduction; long-input/source-selection tests | Omitted text is explicitly unreviewed; no full-native-page performance claim |
| C4 | Implemented | Schema-invalid JSON objects/support mismatches retained in review; validation blocks approval; invalid-shape workflow/API tests | Malformed JSON/envelopes/refusals remain terminal failures, not editable proposals |
| C5 | Partial | List discovery splits supported profile links into per-doctor jobs | One proposal per profile; no multi-doctor extraction when list entries have no separate profile |
| C6 | Implemented | Conservative h1/title/og-title fallback with visible literal support, conflict/hidden-content tests | Metadata alone is not evidence; ambiguous names remain unresolved |
| C7 | Partial | Three provenance-tracked minimal Max factual excerpts; eight offline `test_real_fixtures.py` cases | Locally reconstructed markup, not native captured HTML; no 10–20 native-page corpus or full-page golden accuracy |
| D1 | Implemented | Extended model/schema/migration/export/prompt fields; extended-fact/evidence/photo tests | Fields remain missing without literal support; no clinical/currentness or image-reuse assertion |
| D2 | Partial | Hospital master/source link; matching supported affiliation can store hospital ID | Exact normalized-name matching only; branch/city and alias reconciliation remain human work |
| D3 | Deferred | Approval continues to permit only provider-claimed registration; gate tests | No NMC/state-registry verification job, permission review, or independent evidence model |
| D4 | Implemented | Source ID/type on snapshots/doctors and workbook, per-source merge provenance; source and export tests | Historical unclassified records are not automatically promoted to official |
| E1 | Implemented | Numbered run counters/reset audit; paused/drained checks and discovery lease fencing; directory/API/adversarial tests | Operator deliberately authorizes each run; reset cannot bypass in-flight/queued work |
| E2 | Implemented | Indexed `Proposal.normalized_name` and SQL lookup; migration/deduplication tests | No fuzzy city/hospital-aware matching or 10,000-record performance benchmark |
| E3 | Implemented | Offset/limit/filter/search API and reviewer UI; API/UI tests | No claim of measured large-queue responsiveness |
| E4 | Partial | Evidence-linked forms, explicit up-to-25 selection/acknowledgment, server rechecks, safe navigation/save shortcuts | 100 records/hour unmeasured; malformed proposals still need JSON repair; no automatic approval |
| E5 | Deferred | PostgreSQL dialect, migrations and opt-in integration tests exist | No PostgreSQL server installed/available for the real backend test; SQLite success is insufficient |
| F1 | Deferred | Shared bearer token and explicit self-entered label disclosure remain | No per-user identity, roles, or SSO |
| F2 | Implemented | Versioned correction/withdrawal UI/API and export quarantine; lifecycle/adversarial tests | Prior downloaded exports need an operator recall/replacement process |
| F3 | Implemented | Explicit reviewed merge with per-source evidence; unmerge withdraws split groups; source-origin/export/lifecycle tests | Names must match; same-name identity still human judgment; nested groups require outer-first split and reapproval |
| F4 | Implemented | Inline identity/evidence/contact/registration/duplicate checklist and bulk acknowledgment | Checklist is human attestation under a shared token, not verified reviewer identity |
| G1 | Deferred | Existing private EC2/systemd/nginx/backup templates retained and documented | No instance provisioned, service/TLS deployment executed, reboot/restore exercise, or sizing proof |
| G2 | Partial | Authenticated JSON `/api/metrics`, CLI `metrics`, UI display, run/failure/review/usage counts; API tests | No scheduled daily summary, alert delivery, Prometheus service, or operator escalation automation |
| G3 | Implemented | Per-call provider/model/token records and optional configured-price estimates; unknown/partial/malformed/concurrency usage tests | Estimates are not invoices; missing provider usage/prices remains unknown; live billing not measured |
| G4 | Partial | Six candidate references and one ordinary backend probe recorded in `LIVE_PILOT_RESULTS.md`/JSON | Probe failed DNS before HTTP: zero backend pages, zero live records/approvals/exports; no completed live E2E |
| G5 | Partial | Written official-only/internal-use policy, permission holds and correction process in `SOURCE_POLICY.md` | No per-source collection/reuse clearance or authorization for wider sharing/publication/outreach; Fortis stays held |

## Exit criteria still open

- Phase 0: no 8-of-10 native-page core-field result; backend DNS/egress and source permissions must be resolved first
- Phase 1: no demonstrated complete hospital-chain discovery or 200 reviewed candidates
- Phase 2: no measured 100 reviews/hour, real PostgreSQL staging run, or server deployment
- Phase 3: registry verification, JS rendering and per-user identity remain deferred; manual refresh, database pacing, and metrics are present but not production-proven

A separate `tests/test_scale_smoke.py` exercises 200 fully synthetic offline queue/review/export records. Programmatic approvals and mocked fetches in that test establish neither actual hospital-chain coverage nor human reviewer throughput. See the final test report for its executed result.

Do not substitute offline test counts, search-index observations, reconstructed snippet hashes, or configuration values for these missing measurements. The safe next step is a permitted, bounded real-backend pilot, followed by actual native-page evidence and reviewer measurements.
