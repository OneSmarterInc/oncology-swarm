# Prompt discovery: setup and operation

Type a request such as `Find 10 cancer hospitals in Delhi and their oncologists`.
The portal finds official hospital websites, reads their doctor pages with the
normal crawler, filters the doctors and saves an Excel file. It is the same design
as the Campus scraper's prompt card, adapted to hospitals and doctors.

## One-time setup (Windows PowerShell, in the oncology-swarm folder)

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
ollama pull qwen3:0.6b
.\.venv\Scripts\python.exe tools\setup_search.py
docker compose -f compose.search.yml up -d
.\.venv\Scripts\python.exe tools\check_prompt_setup.py --live-model
.\.venv\Scripts\python.exe -m oncoscraper serve
```

The default `.env` uses `MODEL_PROVIDERS=ollama` and `SEARXNG_URL=http://127.0.0.1:8080`.
If you already run SearXNG for the Campus scraper on port 8080, reuse it: skip the
two search setup lines. Cloud models (Groq, Gemini) are used only when you list
them in `MODEL_PROVIDERS`; a key alone does not switch them on. Never commit `.env`.

Open <http://127.0.0.1:8000/#new>. The prompt card is at the top.

## The two engines

| Engine | Needs | What it does |
|---|---|---|
| Crawl each site (default) | SearXNG only | Searches, then reads each candidate site. A hospital is listed only when the extractor actually reads doctors (or oncologists) from its own pages. |
| AI judges each page | SearXNG + Ollama | The model reads each page and must quote it literally as evidence. Pick this for conditions about individual doctors (email required, experience, language). |

## Rules that never change

1. URLs come from search results or links seen on pages. A model never invents a URL.
2. Names, phones and emails come only from what the hospital page publishes. A model never writes a contact.
3. Doctor portals (Practo, Justdial, Lybrate, Credihealth, Vaidam and similar) are never a source.
4. Discovered sites are checked for public DNS on every request and redirect.
5. robots.txt is obeyed.
6. Shortfalls (fewer hospitals than asked, blocked sites, doctors in other cities of a chain) appear in Coverage notes.

## Stages on screen

1 Understand → 2 Find hospitals → 3 Extract doctors → 4 Match criteria → 5 Save Excel.
Pause, Stop and Resume work at every stage. Progress is saved in
`results/prompt-discovery.db`, so a restart shows the run as interrupted and Resume
carries on. Prompt runs appear in History with the label Prompt.

Uncheck **Also extract the doctors** to get only the hospital list. **Download hospital
list** gives an Excel in the normal upload template, so it can run as a normal batch.

## When something fails

| Message | Fix |
|---|---|
| Setup needed | Start Ollama and SearXNG, check `.env`, restart `serve`. |
| Waiting for service capacity | Search or model is busy. Keep the server on; it retries. |
| No matching sources found | Read Coverage notes. Try a wider prompt or raise Search queries. |
| Requested N hospitals; M found | Raise Maximum hospitals or Search queries. |

`docker compose -f compose.search.yml logs --tail 80` shows search errors.
