import pytest

from attorneyscraper.models import RunConfig
from attorneyscraper.store import Store


@pytest.mark.parametrize("state", ["queued", "fetching", "done", "error", "skipped"])
def test_progress_accounts_for_every_page_state(tmp_path, state):
    store = Store(tmp_path / "test.db")
    rid = store.create(
        RunConfig(
            sources=[
                {"organization": "Firm", "url": "https://firm.com/person"},
                {"organization": "Firm", "url": "https://firm.com/person"},
            ]
        )
    )
    with store.db() as db:
        db.execute("UPDATE pages SET status=? WHERE run_id=?", (state, rid))
    p = store.get(rid)["progress"]
    assert p["starting_total"] == 1
    assert p["starting_finished"] == int(state in {"done", "error", "skipped"})
    assert p["discovered"] == 1
    assert (
        p["successful"] + p["failed"] + p["skipped"] + p["waiting"] + len(p["reading"])
        == 1
    )
    assert len(p["reading"]) == int(state == "fetching")


def test_discovered_pages_do_not_inflate_starting_progress(tmp_path):
    store = Store(tmp_path / "test.db")
    rid = store.create(
        RunConfig(sources=[{"organization": "Firm", "url": "https://firm.com/"}])
    )
    with store.db() as db:
        db.execute("UPDATE pages SET status='done' WHERE run_id=?", (rid,))
        db.execute(
            "INSERT INTO pages(run_id,host,url,depth,priority) SELECT run_id,host,? ,1,0 FROM pages WHERE run_id=?",
            ("https://firm.com/new", rid),
        )
    store.set_status(rid, "completed_with_issues")
    p = store.get(rid)["progress"]
    assert p["starting_finished"] == p["starting_total"] == 1
    assert p["waiting"] == 1
    assert p["discovered"] == 2
