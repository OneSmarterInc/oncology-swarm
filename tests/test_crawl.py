from oncoscraper.config import ScrapeConfig
from oncoscraper.crawl import Crawler

ONCOLOGISTS = {
    "Dr. Meenu Walia", "Dr. Rahul Verma", "Dr. Kavita Menon", "Dr. Asha Rao", "Dr. Priya Nair", "Dr. Anil Gupta",
    "Dr. Harish Chandra", "Dr. Lata Krishnan", "Dr. Imran Qureshi", "Dr. Farah Siddiqui", "Dr. Vikas Malhotra",
}
NOT_ONCOLOGY = {"Dr. Sunil Kumar", "Dr. Neha Joshi", "Dr. Arjun Shah", "Dr. Ritu Bansal"}


def cfg(**kw):
    base = dict(max_pages=60, delay_seconds=0, allow_private_hosts=True, render="never", retries=0)
    base.update(kw)
    return ScrapeConfig(**base)


def need_browser():
    """Skip browser tests when Playwright or its Chromium build is missing."""
    import pytest
    from oncoscraper.render import BrowserRenderer
    try:
        BrowserRenderer(ScrapeConfig()).close()
    except Exception as exc:
        pytest.skip(f"browser unavailable: {exc}")


def by_name(result):
    return {d.get("name"): d for d in result.doctors}


def test_full_site_crawl_finds_every_oncologist(site_url, hits):
    result = Crawler(cfg()).run([site_url])
    docs = by_name(result)
    onco = {d.get("name") for d in result.oncologists}
    assert ONCOLOGISTS <= onco, ONCOLOGISTS - onco
    assert not (onco & NOT_ONCOLOGY)
    # No duplicates of the same doctor across listing, department and profile pages.
    names = [d.get("name") for d in result.doctors]
    assert len(names) == len(set(names))
    # Robots: /private/ never fetched, ?speciality= filter never fetched.
    assert not any(h.startswith("/private") for h in hits)
    assert not any("speciality=" in h for h in hits)
    assert "Dr. Secret Person" not in docs


def test_profile_pages_win_over_cards(site_url):
    docs = by_name(Crawler(cfg()).run([site_url]))
    mw = docs["Dr. Meenu Walia"]
    assert mw.page_type == "profile"
    assert mw.get("experience_years") == "28"
    assert mw.get("qualifications")[:3] == ["MBBS", "MD (Medicine)", "DM (Medical Oncology)"]
    assert mw.get("languages") == ["English", "Hindi", "Punjabi"]
    assert mw.get("consultation_fee") == "1800"
    assert "Breast Cancer" in mw.get("expertise")
    assert len(mw.found_on) >= 1  # also seen on listing/department pages
    rv = docs["Dr. Rahul Verma"]
    assert rv.get("hospital") == "Sunrise Hospital, Gurugram"
    assert rv.get("experience_years") == "15"
    assert "Head and Neck Oncology" in rv.get("sub_specialties")


def test_start_from_a_single_profile_url(site_url):
    result = Crawler(cfg(max_pages=1, use_sitemaps=False)).run([site_url + "/doctor/dr-priya-nair"])
    docs = by_name(result)
    pn = docs["Dr. Priya Nair"]
    assert pn.get("department") == "Gynaecologic Oncology"
    assert pn.get("email") == "priya.nair@sunrise-hospital.example"
    assert pn.get("city") == "Kochi"
    assert result.stats["pages_fetched"] == 1


def test_page_budget_is_respected(site_url):
    result = Crawler(cfg(max_pages=5)).run([site_url])
    assert result.stats["pages_fetched"] <= 5


def test_javascript_page_needs_browser(site_url):
    """Raw HTML has no doctors; with render=auto a headless browser fills them in (if Playwright is installed)."""
    import pytest
    plain = Crawler(cfg(max_pages=1, use_sitemaps=False, render="never")).run([site_url + "/js-team"])
    assert plain.doctors == []
    need_browser()
    rendered = Crawler(cfg(max_pages=1, use_sitemaps=False, render="auto")).run([site_url + "/js-team"])
    names = {d.get("name") for d in rendered.oncologists}
    assert names == {"Dr. Sameer Kulkarni", "Dr. Ritika Bose"}
    assert rendered.pages[0].note == "rendered"


def test_firewall_403_falls_back_to_real_browser(waf_url):
    """Sites like maxhealthcare.in answer 403 to non-browser clients, including for robots.txt."""
    import pytest
    blocked = Crawler(cfg(max_pages=1, use_sitemaps=False, render="never")).run([waf_url + "/doctor/dr-meenu-walia"])
    assert blocked.doctors == [] and "403" in blocked.pages[0].note
    need_browser()
    crawler = Crawler(cfg(max_pages=4, use_sitemaps=False, render="auto"))
    result = crawler.run([waf_url + "/doctor/dr-meenu-walia"])
    docs = {d.get("name"): d for d in result.doctors}
    assert docs["Dr. Meenu Walia"].get("experience_years") == "28"
    assert crawler.fetcher.browser_hosts  # later pages went straight to the browser
    # robots.txt was read through the browser and still obeyed
    assert "rules applied" in next(iter(crawler.fetcher.robots_status.values()))
    assert not any("/private" in p.url and p.page_type != "error" for p in result.pages)


def test_plain_http_cannot_connect_falls_back_to_browser(site_url, monkeypatch):
    """Certificate, proxy or antivirus problems break plain HTTP on some PCs; Chrome still connects."""
    import httpx
    from oncoscraper.fetch import Fetcher

    def broken(self, url):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")

    monkeypatch.setattr(Fetcher, "_raw_get", broken)
    failed = Crawler(cfg(max_pages=1, use_sitemaps=False, render="never")).run([site_url + "/doctor/dr-anil-gupta"])
    assert failed.doctors == [] and "CERTIFICATE" in failed.pages[0].note
    need_browser()
    crawler = Crawler(cfg(max_pages=3, use_sitemaps=False, render="auto"))
    result = crawler.run([site_url + "/doctor/dr-anil-gupta"])
    assert result.pages[0].page_type != "error"
    assert result.doctors
    assert crawler.fetcher.browser_hosts


def test_stops_early_when_pages_stop_showing_doctors(site_url):
    crawler = Crawler(cfg(max_pages=200, stale_pages=2))
    result = crawler.run([site_url + "/"])
    assert crawler.stopped_stale and result.stats["finished_early_no_more_doctors"]
    assert result.oncologists and result.stats["pages_fetched"] < 18  # the full crawl reads 18 pages
