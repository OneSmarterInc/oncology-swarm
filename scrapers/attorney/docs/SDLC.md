# Development and release record

## Scope baseline

Build a separate, local Windows-friendly healthcare attorney research platform, following the OncoScraper/CampusScraper workflow. Defaults: US targets, rule-based extraction, public professional contacts, no paid API requirement, local SQLite history, port 8002. Preserve the original projects unchanged.

The user explicitly requested unit tests, integration tests, real-data simulation and SDLC discipline. This release contains implementation, repeatable tests, CI configuration, a real-source pilot, operational instructions, and known limits. It does not claim that deployment-specific acceptance or a security certification has been completed.

## Requirements and acceptance trace

| ID | Requirement | Implementation | Verification |
|---|---|---|---|
| R01 | Eight overlapping categories with individual evidence | classify.py | Parametrized category unit tests; generic fraud and ambiguous-role negatives |
| R02 | Reject non-attorney staff | classify.py / extract.py | Paralegal unit case and real negative-control biography |
| R03 | Published contacts with correct owner | extract.py | Footer, organization schema, assistant, paralegal, fax, obfuscation tests; live pilot |
| R04 | Avoid speculative contacts | extract.py | No-contact fixture remains empty; only parsed source values exported |
| R05 | Multiple source websites and bounded discovery | models.py / engine.py | Source validation, explicit seeds first, page/depth budgets, mock-site crawl |
| R06 | Durable job controls | store.py / engine.py | Pause/resume, crash recovery, queued-page recovery and atomic page writes |
| R07 | Accurate progress and visible failures | engine.py / portal | Errors, site stop reasons, limited status and page logs |
| R08 | Dedupe without unsafe homonym merging | models.py / store.py | Duplicate observation idempotency, shared contacts, distinct profile URLs |
| R09 | Human review with audit trail | store.py / web.py | API review and audit assertions; browser review flow |
| R10 | Filtered exports with evidence | export.py / web.py | CSV injection, XLSX sheets/row counts, directory filters, browser download |
| R11 | Local access and fetch restrictions | web.py / fetch.py | CSRF, host/origin rejection, private IP, redirect, robots, size limits |
| R12 | Efficient independent website processing | engine.py | Controlled serial/parallel benchmark with equal records |
| R13 | Reproducible real-data check | scripts/real_pilot.py | Five official biographies, four attorneys and one non-attorney control |
| R14 | Repeatable setup and regression gates | requirements, scripts, CI | Local commands and recorded results; Windows/remote CI pending |

## Architecture

The browser UI calls a FastAPI service. Request schemas validate sources/settings. A job manager permits one active batch, with a bounded thread pool across websites. Each website owns a sequential fetcher, robots policy and pacing state. SQLite owns durable frontier rows, observations and review events. Extractors are pure HTML-to-data functions. The directory merges observations conservatively and applies explicit human review decisions. Exports read the same directory model as the portal.

Modules are separated by responsibility: models, classification, extraction, fetching, persistence, orchestration, export, API and UI. Tests use an injected HTTP transport or fetcher, so offline runs exercise the real orchestration/database/API without calling external websites. Live validation uses actual remote HTTP responses and robots files.

## Storage and state

Schema version 1 is recorded in `schema_versions`. Tables: runs, sites, pages, observations, reviews, audit. Foreign-key cascades preserve relational consistency. WAL mode permits reads during crawl writes. Each page completion writes observations and newly discovered links with its terminal page status in one transaction.

Run states: queued → running → completed / completed_with_issues / failed. Pause and stop pass through pausing/stopping until in-flight work is checkpointed. A restart maps active states to interrupted and requeues fetching pages. Human review is separate from extraction observations, so original evidence remains available.

Future schema changes must use versioned, transactional migrations and a pre-migration SQLite backup. Do not introduce unversioned destructive table changes. The initial release needs no legacy-data migration because it has a separate database.

## Quality strategy

Unit tests exercise classifiers, names, contacts, URL normalization, robots rules, export safety and settings. Integration tests run the full source→crawl→extract→SQLite→API→review→export path. Regression fixtures model real layout failures. Browser acceptance uses the real local app/API and a deterministic external HTTP fixture. The live pilot provides complementary evidence for actual network responses and source layouts.

Expected results are explicitly stated before comparison in the live pilot: five names, five published emails, four confirmed attorneys, one excluded paralegal, three category types, support-contact ownership and fax rejection. A passing pilot means these checks passed for the observed sample, not that the crawler has 100% general precision or recall.

Changes identified by tests are fixed with regression coverage, including service-link headings mistaken for names, plural hospital keywords, hyphenated names, split fax labels, support-contact context and short directory pages.

## Security and data handling

The tool is intended for the local desktop only. It rejects credential-bearing/non-HTTP URLs, nonstandard ports, local/private addresses and cross-site redirects. Robots checks are conservative. Response/request bodies are bounded. HTML output uses DOM text nodes, not untrusted HTML. CSV and spreadsheet cells escape formula prefixes. SQL uses bound parameters. Mutations require an in-memory session token and reject foreign origins. The server's default/CLI bind address is loopback.

Do not use the local network checks as a substitute for a production egress firewall. DNS validation and the subsequent connection are separate operations; a hardened public deployment would need DNS-pinned transport or network-level allowlisting to close rebinding/TOCTOU risk. No user authentication or multi-tenant isolation is claimed.

The real pilot's optional remote-DNS adapter exists because this execution environment resolves public names at its SOCKS proxy rather than through local DNS. It restricts the pilot to two explicitly audited hosts. It is not an application setting and does not change normal fetch validation. Live-pilot reporting records whether it was used.

Only public professional data is collected. Organization contacts are not labeled as individual contacts. No emails are sent. Mailbox deliverability, active bar status and licensing are separate verification tasks. Storing minimal contact facts and source evidence is preferred over distributing full biography snapshots.

## Performance and operational limits

Concurrency is across hosts, with one request per host at a time. Multiple seed rows for a host share one worker. Every page is checkpointed. Response sizes, number of source rows, page depth, pages per site and frontier size are bounded. Large runs may need indexed/paginated SQL projections instead of the current in-memory directory aggregation; benchmark on the intended dataset before increasing limits or hosting publicly.

## Release gates and rollback

1. Make isolated changes in a separate branch/repository; retain the original scrapers unchanged.
2. Run unit/integration/regression tests and compile/JavaScript syntax checks.
3. Run browser acceptance for UI/API changes and inspect desktop/mobile screenshots.
4. Run the opt-in live pilot when extraction/fetching changes; report blocked/changed sources honestly.
5. Review the diff, dependency list, secrets exclusions and documented limits.
6. On Windows, install into a clean venv and execute the same automated tests. Remote CI is a separate gate.
7. Back up SQLite before replacing an existing installation. Keep the previous release until acceptance.
8. Roll back code and restore the matching pre-migration database if future migrations are incompatible.

SWARM integration adds this module under scrapers/attorney on feature/attorney-scraper. The existing root oncology application remains in place. CI is scoped to this module. Importing the other scrapers is future work.
