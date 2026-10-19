# Optional OpenAI and Mistral proposal adapters

Both adapters are implemented behind a provider interface and covered with mocked responses. **They are disabled by default and no paid/live provider call was performed for this release.** Model compatibility and real-page accuracy remain unverified. Keep `LLM_ENABLED=0` for the demo and initial source-fetch pilot.

## Deliberate configuration

Choose exactly one provider and an explicit model supported by your account. The examples intentionally contain no model choice or key:

```text
LLM_ENABLED=0
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
MISTRAL_API_KEY=
MISTRAL_MODEL=
MAX_LLM_CALLS=20
LLM_MAX_INPUT_CHARS=40000
LLM_MAX_OUTPUT_TOKENS=4000
LLM_INPUT_PRICE_PER_MILLION=
LLM_OUTPUT_PRICE_PER_MILLION=
```

For Mistral, set `LLM_PROVIDER=mistral` and its model/key instead. Enable only after source-data transmission, provider terms/settings, model compatibility, and spending have been reviewed; restart both API and worker after configuration changes. A provider key is not the application `ADMIN_TOKEN`. Do not share keys through source pages, URLs, screenshots, proposals, or release archives.

An enabled model replaces deterministic extraction for new proposals; failure does not silently fall back. `MODE=fixture` controls source fetching only: enabling a model can transmit synthetic text and incur charges too. An eligible unchanged refresh is detected before reserving/calling a model. That shortcut requires an approved, active, unquarantined original; reverting to an earlier hash after quarantine still needs fresh extraction/review. Changed refresh evidence is saved and the old record quarantined before model work, so provider failure cannot keep the observed stale record exportable.

## Fixed transmission destinations

- OpenAI: `https://api.openai.com/v1/responses`
- Mistral: `https://api.mistral.ai/v1/chat/completions`

The adapter sends selected visible source text, URL, retrieval timestamp, synthetic flag, fixed instructions, and strict JSON proposal schema. Public professional contacts in the selected text may be included. The key goes only in the selected provider's HTTPS authorization header. Input cannot select an endpoint or invoke tools. Redirects, environment proxies, custom CA environment settings, arbitrary base URLs, uploads, conversations/sessions, and automatic retries are not used. TLS verification stays enabled.

OpenAI requests specify `store=false`; this is not a zero-retention guarantee. Neither implementation establishes an account-level retention policy or permission to transmit source content. Review the provider's current applicable terms/data controls yourself before enabling. Do not send patient data, private profiles, credentials, or material outside the approved research scope.

## Literal bounded input and review

The default input-text bound is 40,000 characters, configurable from 1 to 100,000. The preparation step can remove uniquely identifiable navigation/footer/cookie blocks and select ranked literal chunks for long pages, retaining document order. Ambiguous repeated text is not removed merely because it resembles boilerplate. Output records warnings when supported by the proposal shape. Omitted text has not been model-reviewed.

The full immutable visible-text snapshot remains the evidence authority. Selected excerpts do not rewrite it. Local validation requires field values and quotations to match the saved snapshot URL/timestamp/text. The model's claims about registration, contact scope, or instructions are not trusted.

A completed valid provider envelope containing a JSON object can produce a **schema-invalid review proposal** with validation errors; the raw object is retained for repair in advanced JSON, and approval is blocked. Unsupported facts and mismatched quotes also remain reviewable. Malformed JSON, non-object output, refusals, tool calls, incomplete/multiple responses, secret echoes, and unsafe transport are terminal failures rather than approvals.

## Limits, usage, and uncertainty

- `MAX_LLM_CALLS` is a durable per-run reservation ceiling, separate from website requests; it is not a currency budget
- A reservation happens before the attempted call and is not refunded automatically, including when an early failure prevents a completed response
- Each extraction invocation makes at most one provider request; 429/5xx, timeouts, malformed output, and refusals are not automatically retried
- Lease recovery after a reserved call fails for inspection rather than automatically risking another charge; eligible manual retry and refresh require explicit possible-additional-charge acknowledgment
- Output bound: 4,000 tokens by default, allowed 16–16,000. Account/model limits can differ; there is no automatic budget increase or model switch
- Provider response bodies are capped at 2,000,000 bytes; proposal JSON text at 512,000 UTF-8 bytes. Provider responses still require identity encoding; source-site gzip/deflate support does not change this adapter rule
- Connect/write/pool timeout is 10 seconds; read inactivity timeout is 60 seconds. A 90-second elapsed check runs after headers/chunks; it is not an independent strict process-kill deadline during a blocking read
- Sanitized errors do not include provider bodies, headers, credentials, or model reasoning

Returned usage is stored per call with provider, model, run, input/output/total tokens where valid, and an optional USD estimate. Both configured USD-per-million rates and usable token counts are required for a cost estimate. Missing counts/prices and inconsistent totals stay unknown; zero is not substituted. `/api/metrics` exposes reserved calls, recorded responses, calls without usage, partial-cost flags, and the sum of available estimates for the current run. Those estimates exclude unreported usage and pricing features not represented by the two configured rates; they are not an invoice or guaranteed spend limit.

Inspect provider-side billing after ambiguous outcomes and configure spending controls there. Offline usage tests cannot prove real billed amounts. Complete a separately authorized small live-provider trial, measure factual support/corrections and record actual usage before expanding.
