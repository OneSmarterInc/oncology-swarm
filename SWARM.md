# SWARM — scraper collection

SWARM is the planned home for the oncology, campus and healthcare attorney scrapers.

## Current integration

- Main retains the existing oncology research application at the repository root.
- feature/attorney-scraper adds Attorney Atlas under scrapers/attorney/.
- The rule-based OncoScraper remains on the existing onco-scraper branch.
- CampusScraper remains in OneSmarterInc/campus_scrapper.
- A combined launcher, dashboard and shared database are future work.

## Recommended direction

Use folders for enduring product boundaries and short-lived branches for changes. After review, merge this feature branch into main. Import other scrapers through separately reviewed migration branches, preserving their histories and behavior. The root oncology research app and the rule-based OncoScraper are distinct implementations; retain both if needed.

Proposed eventual folders: scrapers/oncology-research/, scrapers/onco/, scrapers/campus/, scrapers/attorney/, shared/ for proven reusable components, and portal/ for an optional unified launcher.

Initially each scraper keeps its own dependencies, virtual environment, database and tests. Extract common fetching, job and export code only after compatibility tests exist. Keep doctor, faculty and attorney classification rules separate. A unified portal can route to individual tools before backend consolidation.

## Attorney quick start (Windows)

From the repository root, change to scrapers/attorney, run setup_windows.bat, then run_ui.bat. Open http://127.0.0.1:8002. See the module README and docs/VALIDATION.md for features, tests and limitations. Use a separate checkout or worktree to run another branch concurrently.

The new CI workflow checks only the attorney module. No existing scraper deployment or database migration is included.
