# Architecture and implemented scope, v2

## Bounded pipeline

```text
Human approves official source + purpose, configures exact hostname
  -> bounded list/pagination/sitemap discovery OR explicit profile URLs
  -> durable candidate jobs -> coordinator with 2–4 threads
  -> safe fetch -> immutable visible-text snapshot + response-content hash
  -> deterministic extraction OR optional OpenAI/Mistral proposal
  -> local evidence validation + indexed duplicate candidates
  -> human review -> active approved doctor -> Excel
```

The API supplies discovery/control/review and the worker is a separate process. Discovery is a bounded synchronous API action with a persisted run and owner/lease heartbeat. Only one discovery run can be active at a time; request reservations, candidate enqueue, and completion are all lease-fenced. Its fetcher shares the worker's database-backed host pacing and durable request counter. It returns candidates and coverage/errors; it does not verify doctors or automatically approve them.

SQLite is the small single-coordinator local demo. PostgreSQL migrations and integration tests are provided for server use, but no real PostgreSQL server was available for this release's verification. Keep one coordinator in the initial rollout and test the actual database/runtime before any horizontal scaling.

“Swarm” means a bounded queue-driven pool, not independent autonomous AI agents. Search engines, recursive site-wide crawling, JavaScript rendering, browser authentication, PDF/OCR, automatic registry verification, scheduled refresh, public publication, and outreach are not implemented. No production-ready or national-coverage claim follows from the name.

## Source/discovery controls

- A source belongs to a hospital master record and stores its exact host, approved list URL, optional sitemap/path prefix, human reviewer/date/note, and official type
- Source approval does not modify `ALLOWED_DOMAINS`; both controls are required for new live jobs
- Explicit live URL entry requires exactly one active approved source for its hostname; discovery supplies its source ID to disambiguate
- Oncology signals are scoped to local profile cards/links; navigation, cross-host, unsafe, hidden, and unrelated links are excluded
- Bounded link-based pagination and XML sitemap/index traversal are supported; JavaScript-only lists are reported as a coverage gap
- The optional profile pattern is a literal path prefix, not a regex or permission to skip oncology evidence
- Candidate count and traversal bounds are separate from `MAX_JOBS` and `MAX_REQUESTS`; inspect every returned error and the `truncated` flag

## Lifecycle and consistency

```text
queued -> leased -> review -> approved
             |        +-> failed (human rejection retained)
             +-> retry / failed
approved -> withdrawn
approved / withdrawn -> review (supported correction)
approved -> merged (explicit contribution to another approved doctor)
merged contribution + target -> withdrawn groups (explicit unmerge)
approved -> new refresh generation -> unchanged OR review
changed refresh approved -> prior job superseded
```

Claims, heartbeats, owner/lease fences, bounded retry attempts, and durable reservations survive restarts. A stale worker cannot commit a new result after its lease is lost. A possibly billable reserved model call is not automatically replayed after lease recovery. Retry of that work requires deliberate acknowledgment of another possible charge.

Proposal edits/review use optimistic versions and shared transactions. Indexed `normalized_name` lookup replaces the old Python-wide proposal scan, but remains a conservative exact-name candidate rule, not fuzzy identity resolution. Cross-city names can match; spelling variants can be missed. Human review decides whether to keep separate identities or merge them.

Withdrawals exclude the doctor from future exports; withdrawing contributing evidence also quarantines a merged target. Corrections reopen supported unmerged records and require a new approval. Merge retains the chosen target identity, moves source-specific affiliations/contacts/registrations, and retains every contributing proposal/snapshot/review. It never guesses a mixed identity. Unmerge splits a contributor from its target and leaves both resulting groups withdrawn; split outer/nested groups until individual records can be corrected and explicitly reapproved.

Manual refresh creates a new URL generation. An equal response-content hash can record an `unchanged` snapshot without another model call or proposal only while the original doctor remains approved, active and unquarantined. Changed fetch evidence is retained and quarantines the old doctor (`needs_review`) before extraction/model work; a later extraction failure cannot leave that doctor exportable. Successful extraction creates a new review proposal; approving it supersedes the old job. A failed refresh with captured evidence is immutable and cannot be retried in place; queue a new refresh from the original. If a later fetch reverts to the original hash after quarantine, it still requires a fresh proposal and explicit reapproval. Rejecting new evidence does not revive the old export. Pending/changed refresh guards prevent stale correction or merge from bypassing quarantine. Merged records require withdrawal and source-level reconciliation before correction or refresh. Refresh is not scheduled automatically and a failed fetch is not proof of freshness.

## Data and provenance

Retained tables include `control`, `jobs`, `snapshots`, `proposals`, `doctors`, `affiliations`, `contacts`, `registrations`, `audit_log`, `hospitals`, `sources`, `doctor_sources`, `discovery_runs`, `host_pacing`, and `llm_usage`.

A snapshot contains normalized visible text, source URL, retrieval timestamp, synthetic/source classification, and a SHA-256 fingerprint of the fetched response content. Original HTML is used during extraction but not retained in the database. The hash is not the hash of normalized visible text, a digital signature, or evidence of independent timestamping. A hash change can reflect nonclinical page changes as well as changed doctor information.

Every populated identity/extended field, affiliation, contact, and registration fact requires exact supporting evidence from that snapshot. Extended identity fields are qualifications, experience years, subspecialty, languages, OPD timings, consultation fee, and photo URL. Source expressions such as “20+ years” remain text; the app does not derive start dates. Photo URLs are references requiring literal evidence and HTTPS, not downloaded or rendered images. Unlabelled or unsupported fields stay missing.

Hospital matching uses normalized names for supported affiliation linking; it does not infer a branch/city or silently collapse aliases. New live jobs record approved source IDs; historical v1 records may remain `unclassified` after migration rather than gaining invented approval history.

Contacts retain `doctor`, `institution_central`, or `unclassified` scope. Registration remains `provider_claimed`; approval rejects `current_independently_verified` and `historical_record` because no independent registry-evidence workflow exists. Schema/provenance validity is not proof of medical truth or professional eligibility.

## Budgets and model use

Request, job, and model-call counters belong to a durable numbered run. They do not reset on restart. An explicit reset is allowed only when paused, no queued/retry/leased work remains, and no active discovery lease exists. Previous totals and the reason remain in audit history. Expired discovery runs are fenced from further requests. The reset does not erase jobs, evidence, source approvals, or host pacing.

Optional model adapters are disabled by default. They send bounded literal excerpts and source metadata only to fixed OpenAI or Mistral endpoints; schema-invalid JSON objects can remain visible for repair with blocking errors. Transport/envelope/malformed-JSON failures are terminal, with no silent fallback or automatic paid retry. Usage records preserve provider/model/token metadata per call; missing usage or prices remains unknown. See [adapter controls](LLM_ADAPTER.md).

## Export contract

Workbook sheets: `Doctors`, `Affiliations`, `Contacts`, `Registration`, `Evidence`, `Sources`, `Review`, `Readme`. Only active, human-approved doctors without `needs_review` are eligible. A shared review lock materializes a consistent dataset before Excel serialization; a correction cannot mix a former approved identity with new unreviewed evidence.

`doctor_id` links sheets. Merged-source evidence IDs are namespaced `job:<job_id>:<evidence_id>`; child records retain their originating proposal. All contributing sources/reviews remain visible. Formula-looking strings are neutralized and cell lengths are bounded. Previously downloaded workbooks cannot be recalled by withdrawal: track their recipients and replace them through an authorized process.

## Remaining verification

See [gap matrix](GAP_MATRIX.md), [test report](../TEST_REPORT.md), and [live results](LIVE_PILOT_RESULTS.md). Offline tests, reconstructed snippets, deployment templates, and configured worker counts are not proof of real-site success, active processes, a deployed server, throughput, or completeness.
