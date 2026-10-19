# Minimal real-source regression excerpts

These three cases contain public professional facts observed on official Max
Healthcare profile pages on 19 October 2026. They are **not captured hospital
HTML**. The ordinary application fetcher failed DNS resolution before an HTTP
request could be reserved. A separate web research tool exposed indexed text.
Only three short factual spans per source were retained (10–11 words per page)
and wrapped in locally authored HTML for an offline regression exercise.

## What the files establish

- `manifest.json` records exact source URLs, observation time, source-text line
  locations, transformations, fixture and excerpt hashes, and known gaps
- `golden.json` records manually transcribed name, one oncology specialty, and one
  hospital/location visible on each source. It is not a complete directory row
- The HTML preserves these short strings, not the native DOM, CSS, JSON-LD,
  context, navigation, or any site-specific structure
- `tests/test_real_fixtures.py` checks provenance, factual spans, name fallback,
  literal evidence, and conservative omission of unlabelled affiliation/specialty

## Limits and handling

No source HTTP status, original retrieval time, or original-page content hash is
available. The web observation time is not the time a backend fetched the source;
the web tool may serve indexed content of the stated age. Fixture hashes identify
only these locally reconstructed files. Do not relabel them as raw captures or
count their tests as live extraction accuracy.

These are real people, so test `FetchResult` objects use `synthetic=False`.
Reconstructed-markup provenance is explicit in this manifest; no application
database is seeded and no real row is approved or exported. The existing two
fictional demo fixtures remain separate and synthetic.

No broad reproduction or redistribution license was established. These minimal
facts are retained for private development and review. No biographies, photos,
private or public contacts, patient stories, appointments, schedules, or fees are
bundled. Review the source terms, intended use, and rights before redistribution
or public directory publication. Robots permission, where obtained, does not
settle reuse rights. Do not automatically crawl source URLs from this folder.

Fortis candidates were inspected as source references but **no Fortis fixtures
are bundled**. Its [terms](https://www.fortishealthcare.com/terms-and-conditions)
contain reuse and benchmarking restrictions requiring a permission decision.

See `docs/LIVE_PILOT_RESULTS.md` for the actual backend result and selected
candidate outcomes. The generated private `reports/live-pilot-v2.json` research
output is intentionally omitted from GitHub; its artifact-only test skips when
absent. The original aim of testing captured native hospital markup remains open.
