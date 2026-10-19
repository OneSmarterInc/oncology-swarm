# Private administrative API, v2

The UI at `/` is the usual review client. Base address is `http://127.0.0.1:8000`, directly or through an SSH tunnel; a restricted valid TLS proxy is optional. Every `/api/...` route requires `Authorization: Bearer YOUR_PRIVATE_TOKEN`. `/healthz` and the static interface shell are unauthenticated. Interactive Swagger/OpenAPI is disabled.

Use JSON and a normal client that supplies `Content-Length`; mutation requests without it are rejected. Body limit: 1,000,000 bytes. Keep tokens out of URLs/logs. Authentication is one shared administrator token, with self-entered reviewer audit labels, not per-person identity or roles.

## Status, queue, and controls

| Method/path | Contract |
| --- | --- |
| `GET /api/status` | Mode, paused state, current run/counters/caps, lifecycle counts, exportable total, backend, allowed hosts, and explicit shared-token identity limitations |
| `GET /api/metrics` | Current run request/job counts; reserved/recorded/missing-usage model calls; token/available cost totals with uncertainty flags; active discovery, review/failure, exportable counts |
| `GET /api/jobs` | Paginated `{items,total,offset,limit}`; default limit 25, allowed 1–100 |
| `POST /api/jobs` | `{ "urls": ["https://approved-host.example/profile"] }`, 1–100 URLs; per-item outcome |
| `GET /api/jobs/{id}` | Job lifecycle/generation, doctor export flags, proposal/version/errors/duplicates, retained snapshot/provenance |
| `PUT /api/jobs/{id}/proposal` | `{ "data": { ...whole proposal... }, "expected_version": 1 }`; review state only |
| `POST /api/jobs/{id}/retry` | `{}` for eligible failed fetch; model-reserved work needs possible-additional-charge acknowledgment |
| `POST /api/control` | `{ "paused": true }` or false; pause does not cancel a request already in flight |
| `POST /api/control/reset-budgets` | Reviewer/note; requires pause, no queued/retry/leased work, no active discovery lease; preserves history and returns new run number |
| `GET /api/export.xlsx` | Active approved-only workbook, excluding withdrawn/quarantined records |

Queue filters: `q` (name or URL text), exact `state`, exact `host` (hostname only), `validation=valid|invalid|missing`, `offset`, `limit`. Empty filters are allowed. Summaries include proposal version, validation error count, duplicate IDs, and `bulk_eligible`. “Valid” means no current validation errors, not professional verification or approval. Configured `worker_count` does not prove that workers are alive.

An already known canonical URL returns its latest existing generation; enqueue does not refetch it. Use explicit refresh. New live URLs require a matching exact host allowlist and exactly one active human-approved source for that host; source-specific discovery supplies its source ID. Fixture mode accepts only the two documented fictional seed URLs.

## Sources and discovery

| Method/path | Contract |
| --- | --- |
| `GET /api/sources` | `{items:[...]}` with source/hospital IDs, name/city, URLs, type, reviewer/date/note, active flag |
| `POST /api/sources` | Human source approval; fields below |
| `POST /api/sources/{id}/discover` | `{ "max_pages": 10, "max_profiles": 200, "use_sitemap": false }` |

Source approval body:

```json
{
  "name": "Approved institution name",
  "city": "Supported city or empty string",
  "hostname": "profiles.hospital.example",
  "list_url": "https://profiles.hospital.example/doctors",
  "sitemap_url": null,
  "profile_url_pattern": "/doctors/",
  "reviewer": "Actual reviewer",
  "note": "Actual ownership, purpose and permission decision"
}
```

These are placeholders, not approved sources. `hostname` must exactly match the list URL and configured allowlist. Optional sitemap must be same host. The optional pattern is a literal path prefix beginning `/`, without query/fragment; it is not a regex or a reason to ignore oncology relevance. New records have official source type; candidate files do not auto-import.

Discovery is synchronous and may take minutes. Only one active discovery is allowed at a time; requests, candidate enqueue and completion are lease-fenced. Do not submit it repeatedly while waiting. `max_pages` range is 1–50; `max_profiles` 1–1000. It follows bounded same-host pagination and optional sitemap/index links through the same fetch/budget controls. Result fields include `profiles` with found-on/label/oncology context, `pages_fetched`, `sitemaps_fetched`, `errors`, `truncated`, `discovery_run_id`, and per-candidate `jobs`. Inspect all errors/truncation and per-job outcomes; a successful response does not certify completeness or successful enqueue of every candidate. JavaScript-only lists may produce no usable profiles.

## Human-review and lifecycle actions

Use current versions read immediately before acting:

```json
{
  "expected_version": 1,
  "reviewer": "Actual reviewer",
  "note": "Checks actually performed and decision",
  "duplicate_acknowledged": false
}
```

Reviewer/note must be nonempty (maximum 100/2000 characters). `expected_version` is a positive integer. Use this review body with:

- `POST /api/jobs/{id}/approve`: revalidates evidence and duplicates; explicit acknowledgment is needed only after comparing flagged duplicates and deciding to retain separate identities
- `POST /api/jobs/{id}/reject`: retains the proposal and audit; state becomes `failed`, not eligible for failed-fetch retry
- `POST /api/jobs/{id}/withdraw`: removes the record from future exports; contributing-source withdrawal also quarantines merged targets
- `POST /api/jobs/{id}/correct`: approved/withdrawn unmerged records return to review; pending/newer changed refresh guards prevent stale reapproval
- `POST /api/jobs/{id}/refresh`: add `acknowledge_possible_additional_model_charge: true` when model use is enabled; creates a new generation of an approved unmerged source

`POST /api/jobs/{id}/merge` and `/unmerge` also require `target_job_id` and `target_expected_version`. Merge needs two active approved versions of the same normalized name and synthetic category, no pending refresh, plus a meaningful human identity decision. It retains target identity and all contributing evidence; same name is a prerequisite, not proof of identity.

Unmerge detaches a contributor from the top-level target and withdraws both resulting groups. Use current versions and split outer groups first if nested. It does not republish either record. Continue source-level reconciliation, correct individual supported proposals, and explicitly reapprove. Direct correction/refresh of still-merged sources remains blocked. The UI supports target inspection and explicit merge/unmerge actions.

Changed fetch evidence is captured and quarantines the previous record before extraction/model work, so even a later extraction failure keeps it out of exports. Approval of the new proposal supersedes the prior job. A failed refresh with retained evidence cannot use `/retry`; create a new refresh from the original. An equal hash records an `unchanged` result without another proposal/model call only if the original is still active, approved and unquarantined. Returning to an earlier hash after quarantine still requires fresh review/reapproval. Rejecting a changed proposal does not restore stale export eligibility. A withdrawal/correction/merge of the original cannot be overwritten by a late refresh approval.

## Bulk approval

`POST /api/jobs/bulk-approve` accepts 1–25 distinct selected versions:

```json
{
  "items": [{"id": 1, "expected_version": 1}],
  "reviewer": "Actual reviewer",
  "note": "Checks performed on every selected version",
  "human_acknowledged": true
}
```

The UI requires explicit item selection and a human checklist acknowledgment. Every selected proposal must be in review, clean, and duplicate-free when rechecked on the server. No duplicate acknowledgment bypass is provided in bulk. The response is per-item, not all-or-nothing: inspect every result/error. Keyboard navigation/save shortcuts never approve or reject.

## Proposal editing and failed calls

The proposal remains `schema_version: 1`; v2 adds optional fields while retaining this envelope. Top-level keys: `schema_version`, `synthetic`, `doctor`, `affiliations`, `contacts`, `registration`, `evidence`, `warnings`. Identity supports name, specialty, city, qualifications, experience years, subspecialty, languages, OPD timings, consultation fee, and photo URL. See `app/validation.py` for exact limits. Every populated fact requires literal source evidence; snapshots cannot be edited.

Updates replace the whole proposal. Preserve supported fields and evidence. Invalid objects can remain reviewable, but no approval is allowed with validation errors. For a structurally invalid model object, use advanced JSON to repair it; a form cannot make an invalid shape safe automatically. Only `provider_claimed` registration is approvable.

A failed job with a previously reserved model call requires `{"acknowledge_possible_additional_model_charge":true}` for manual retry. Inspect provider billing first: an interrupted result may already have been billed. This acknowledgment does not refund/reset counters. Transport/envelope failures are not automatic paid retries.

## Error handling

`401` invalid token; `404` unknown detail ID; `409` lifecycle/version/budget/pause conflict; `411` missing Content-Length; `413` oversized body; `422` invalid request schema/value. Reload after conflicts and reconcile newer data. A source/security/network refusal is not permission to loosen checks. The shared token grants the full API capability set.
