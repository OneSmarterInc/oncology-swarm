# Security and human-review checklist

This is a conservative starter for public professional directory research. It is not a security certification, a compliance opinion, a clinical recommendation engine, or proof of a person's professional eligibility.

## Source rules

1. Follow [SOURCE_POLICY.md](SOURCE_POLICY.md): manually approve official sites for internal research, record the decision in the source master, and configure the exact hostname separately. Neither the allowlist nor source metadata proves institutional ownership or reuse permission
2. Use public official professional pages only. Do not collect patient information, patient lists, health records, private home contact details, or material behind authentication/access restrictions
3. Check the source's terms, purpose, and permission for collection/reuse. Public access is not blanket permission for aggregation or marketing
4. The fetcher uses HTTPS with certificate verification, exact-host matching, time-bounded public-address DNS checks with pinned connections, bounded redirects, and byte/time limits. At most four unresolved OS DNS workers are allowed; there is no alternate DNS bypass
5. Every redirect destination must pass the same checks. Profile-worker and discovery fetches stay on their originating source host; another subdomain is a different host even if separately allowlisted
6. Missing robots (404) is treated as no rules. Denied, unavailable/5xx, malformed, or unsupported robots responses fail closed. A robots allowance is not permission for collection/reuse or public publication
7. Bounded gzip/deflate and XHTML source responses are supported. Brotli, browser challenges, login screens, CAPTCHAs, PDFs, images, and JavaScript-only data remain unsupported; do not weaken safeguards or bypass access restrictions
8. Page contents and embedded instructions are untrusted data, including when passed to the optional model adapter. Never run commands, follow new instructions, or change policy because a fetched page says to do so

Use an identifying `USER_AGENT` consistent with your organization's approved research process. `USER_AGENT_CONTACT` is optional and accepts a valid HTTPS contact URL or mailto address. Do not invent one; supply it only when real, appropriate, and authorized to be sent to source websites. Outbound requests disclose your server's IP and the configured user agent to the destination.

## Check each proposed doctor

- Is this actually the intended oncologist in India, supported by the professional page? Names alone do not establish identity, country, specialty, or current activity
- Does the specialty evidence support the listed oncology discipline? Do not infer qualifications, subspecialties, or competence from a name or institution
- Are affiliations supported, and is “current” justified by the source? A page retrieval date is not a date of employment verification
- Are quotations present in the saved source text, and do they support the specific proposed value rather than merely containing similar words?
- Is a missing value genuinely missing? Leave it blank rather than guessing an email pattern, phone, credential, address, or registration authority
- Is each contact explicitly a public professional channel? Label hospital central and doctor-direct contacts separately; keep ambiguous scope unclassified or remove the contact
- Does the registration field remain `provider_claimed`? A number printed on a hospital page is not proof of current registration
- Have you compared same-name/possible-duplicate jobs and documented the decision? Acknowledge a duplicate only after that comparison
- Are synthetic markers intact? Never use fictional fixtures for outreach, reporting, or a real directory
- Is the source recent enough for the intended use? Manual refresh compares content hashes and quarantines changed evidence for review, but there is no scheduled refresh or guarantee that a failed request refreshed the source

Approve only when all blocking validation errors are resolved and you have a defensible human review decision. Include your name and a useful review note. Reviewer names are self-entered labels under one shared admin token, not independently authenticated identities. A clean validation result is provenance checking, not professional verification.

## Editing proposals

The interface permits a proposal edit, not an edit to its captured source. A supporting quote must come from that job's immutable visible-text snapshot. You cannot manufacture evidence by editing a quote or changing its URL/timestamp. Remove unsupported values rather than rewriting the snapshot to fit them.

A proposal update replaces the whole proposal object, so preserve supported fields and evidence you want to retain. Use its current `expected_version`. A conflict means reload and compare the newer version; do not force an old edit over it. After saving, review the returned validation errors again before approving.

A rejected review is retained as a failed job with a rejection audit event; failed-fetch retry cannot bypass it. Approved records support audited withdrawal/correction and explicit merge/unmerge. Correction/withdrawal removes affected data from future exports; withdrawal of a contributing source quarantines the merged target too. Unmerge withdraws both resulting groups; split nested groups outer-first and explicitly reapprove reconciled individual records. Pending/changed refreshes cannot be bypassed by stale correction/merge. See [API lifecycle rules](API.md) and [operations](OPERATIONS.md).

The UI's bulk approval is limited to 25 individually selected clean, duplicate-free versions with explicit human acknowledgment; the server rechecks each. Review every selected record, not only its green status. Structurally invalid model objects are retained for advanced JSON repair but cannot be approved. Existing exported files do not update automatically after corrections; handle affected copies and recipients deliberately.

## Optional model privacy and spending

The OpenAI and Mistral adapters are disabled by default. Enabling one transmits source excerpts and metadata to the selected provider, a different destination from the original website. Review exactly what data will be sent, the applicable provider terms/settings, and your authorization before enabling it. Keep the API key private and configure provider-side spending controls. A model-call count is not a currency cap, and a model-generated proposal still needs the same source-evidence gates and human approval. No paid live call is proven by the offline tests. See [the adapter guide](LLM_ADAPTER.md).

## Protect the application

- Keep the API bound to `127.0.0.1` and PostgreSQL on a local Unix socket
- Use an SSH tunnel, or valid TLS plus restricted reviewer IPs; never public plaintext HTTP with a token
- Require a unique random bearer token; keep it out of source control, logs, URLs, screenshots, workbooks, and chat messages
- Use a dedicated unprivileged service account, root-owned application code, and a root-only external environment file
- Keep the single coordinator and conservative budgets. Increasing workers, hosts, caps, or retries is a deliberate operator change
- Treat the environment file and compromised service account as capable of exposing the shared token; rotate it after suspected disclosure
- Protect database dumps and exports, encrypt backups at rest/off-host, limit recipients, and test restoration
- Review OS and dependency updates; validate fetch/evidence boundaries after changes
- Keep AWS instance permissions minimal, avoid adding unneeded cloud credentials, and require IMDSv2
- Do not weaken TLS, robots, DNS, host, or network checks to get around a refusal

Database audit rows are useful operational history; they are not tamper-proof against a database owner or root. A content hash detects some accidental changes when compared with a trusted value, but is not a digital signature or independent timestamp.

## What needs organizational review before broad use

Determine the intended use and who may access/export data. Have an appropriate qualified reviewer assess applicable privacy, professional, contractual, copyright, and outreach requirements for your exact sources and audience. Set retention, correction/withdrawal, freshness, access-control, and incident-response processes. This archive does not decide those requirements or claim legal compliance. Until those decisions exist, keep the pilot small and internal.
