# Source and use policy for v2

## Default scope

This release is scoped to **small, internal, human-reviewed research from official public institutional sites**. Aggregators, social profiles, patient content, private directories, authenticated material, and guessed contacts are outside this release's approved source strategy. No source is pre-approved by inclusion in this archive.

This is an operational boundary, not a legal opinion or a declaration that scraping/reuse is lawful. Public availability, a robots allowance, a search result, official branding, or an application source record does not independently grant collection, retention, model-sharing, redistribution, publication, or outreach rights. Obtain the appropriate permission/review for the particular source, purpose, audience, and data before collecting it.

## Two human decisions, plus technical controls

1. Verify institutional ownership of the exact hostname and the intended list/profile URLs
2. Record the approved purpose, permitted fields, terms/permission basis and limitations, reviewer, date, retention, and correction contact/process
3. Add that exact hostname to configuration only after the decision; do not add whole domains/wildcards or adjacent redirect hosts automatically
4. Create the human-approved official source/hospital record with a substantive note. This does not edit the runtime allowlist or approve any resulting doctor proposal
5. Discover or seed a bounded set of candidate URLs. Every fact and contact scope still requires evidence and explicit review before export

The software records a reviewer-supplied decision; it does not authenticate the reviewer's identity or adjudicate source ownership/permissions. Human approval must not be fabricated by importing a candidate list, a model suggestion, or a metadata fixture. Sources in the pilot report remain UNAPPROVED and must not be automatically imported.

## Collection boundaries

Respect robots, published restrictions, rate limits, and access controls. The fetcher treats missing robots (404) as no robots rules, while forbidden/unavailable/malformed cases remain blocked conservatively. Robots behavior is a transport rule, not a reuse license. Do not bypass login, CAPTCHA, TLS warnings, network restrictions, or site refusals with an alternate transport.

Use an honest identifying user agent. Add a real HTTPS contact URL or mailto address only when authorized; the app permits leaving it blank rather than inventing contact details. Requests disclose the server IP and user agent to the destination.

Limit records to public professional facts needed for the internal purpose. Do not collect patient information, health records, home/private contact details, or sensitive personal stories. Preserve unsupported values as missing; a hospital central line is not a doctor's direct line. Registration on a profile remains provider-claimed, never automatically current/independently verified.

## Reuse and sharing

- A record approval only makes that reviewed version eligible for a private export
- No automatic approval, public directory publication, contact outreach, marketing, appointment submission, or external sharing is implemented or authorized
- Model extraction is separate transmission to OpenAI or Mistral and needs its own source/data/account authorization; it is off by default
- A photo URL is an evidence-backed reference only; there is no image download/republication permission
- Protect exports and backups, share only with explicitly authorized recipients, and keep a record of where copies went
- Establish purpose-specific retention, access, correction, and deletion procedures before broad use; this application has no automatic retention/deletion or public opt-out portal

## Corrections and objections

Assign a real operator to receive correction/withdrawal requests through your approved channel before launch; this archive does not invent or publish a contact address. Check the claim, pause affected use when necessary, and use audited withdrawal/correction. Withdrawn and quarantined records disappear from future exports, but existing workbooks do not update themselves. Replace or recall distributed copies through an authorized recipient-specific process. Retain only the evidence/audit material allowed by the established retention decision; do not silently alter database history.

## Pilot-specific holds

The [pilot results](LIVE_PILOT_RESULTS.md) record six manually selected references, one DNS-blocked backend attempt, and no backend captures. Max and Fortis source-master candidates remain unapproved. Fortis automated probes/fixture reuse are held for an operator permission decision after its published restrictions were identified. Three short reconstructed Max factual-excerpt fixtures support private regression work only; no broad reuse license or native-HTML capture is claimed.

Before publication, outreach, model transmission, or a wider crawl, obtain a new decision covering that expanded action, the recipients/audience, the relevant data, and the applicable terms. This release supplies no automatic expansion of authority.
