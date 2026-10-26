"""Cheap search triage. Ranking is a hint; the hospital's own pages still decide eligibility."""
import re
from collections import defaultdict, deque
from urllib.parse import urlsplit


REFERENCE_HOSTS = (
    "dictionary.cambridge.org", "dictionary.com", "thesaurus.com",
    "merriam-webster.com", "collinsdictionary.com", "wiktionary.org",
    "oxfordlearnersdictionaries.com", "dictionary.langeek.co", "wikipedia.org",
)
# Doctor-listing and booking portals. They may suggest a hospital NAME; they are never a source.
AGGREGATOR_HOSTS = (
    "practo.com", "justdial.com", "lybrate.com", "credihealth.com", "medifee.com", "vaidam.com",
    "clinicspots.com", "hexahealth.com", "docprime.com", "sehat.com", "bookmerilab.com", "medindia.net",
    "indiamart.com", "quora.com", "facebook.com", "linkedin.com", "instagram.com", "youtube.com",
    "twitter.com", "x.com", "scribd.com", "glassdoor.co.in", "glassdoor.com", "ambitionbox.com",
    "mfine.co", "tata1mg.com", "1mg.com", "pristyncare.com", "medicoverhospitals.in.reviews",
    "zoom-hospitals.com", "hospitalsinindia.co", "drlogy.com", "kivihealth.com", "joonsquare.com",
    "sulekha.com", "magicpin.in", "google.com", "maps.google.com",
    # Doctor finders, ratings and medical-tourism agents: they list other hospitals' doctors.
    "webmd.com", "healthgrades.com", "vitals.com", "zocdoc.com", "ratemds.com", "doximity.com",
    "cancerfax.com", "lyfboat.com", "medmonks.com", "indiamedtrip.com", "medicaltourismco.com",
    "placidway.com", "bookimed.com", "mediglobus.com", "airrova.com", "doctoriduniya.com", "myupchar.com",
    "healthline.com", "mayoclinic.org", "clevelandclinic.org",
    # Research and information sites, not hospitals.
    "cancer.gov", "nih.gov", "cancer.org", "who.int", "medlineplus.gov",
)


def _matches(host, hosts):
    return any(host == h or host.endswith("." + h) for h in hosts)


def exclusion_reason(url):
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "Malformed source URL"
    if _matches(host, REFERENCE_HOSTS):
        return "Reference/dictionary site, not an official hospital source"
    if _matches(host, AGGREGATOR_HOSTS):
        return "Third-party doctor listings are not official hospital sources"
    return ""


def rank_urls(urls, details, context):
    """Prefer relevant hospital pages, then interleave hosts to avoid one-site floods.

    No hospital, city or TLD allowlist; titles/snippets are never accepted as
    evidence of ownership or location.
    """
    valid = []
    for url in dict.fromkeys(urls):
        try:
            parts = urlsplit(url)
            if parts.scheme in {"https", "http"} and parts.hostname and not parts.username and not parts.password:
                valid.append(url)
        except ValueError:
            continue
    terms = set(re.findall(r"\w{3,}", context.casefold())) - {
        "the", "for", "and", "from", "with", "find", "must", "site", "https", "www",
        "doctors", "doctor", "official", "website", "urls", "hospital", "hospitals",
    }

    def score(url):
        d = details.get(url, {})
        text = " ".join([url, d.get("title", ""), d.get("content", "")]).casefold()
        words = set(re.findall(r"\w{3,}", text))
        return (6 * len(terms & words)
                + 5 * bool(re.search(r"hospital|cancer cent|institute|medical|health", text))
                + 4 * bool(re.search(r"oncolog|doctor|consultant|specialist|find-a-doctor|our-doctors", text)))
    groups = defaultdict(deque)
    for url in sorted(valid, key=score, reverse=True):
        groups[urlsplit(url).netloc.lower()].append(url)
    ordered = []
    while groups:
        for host in list(groups):
            ordered.append(groups[host].popleft())
            if not groups[host]:
                del groups[host]
    return ordered


def relaxed_queries(queries, searched):
    """Remove exact-phrase/TLD syntax for another search, never change the plan."""
    output = []
    for query in queries:
        relaxed = re.sub(r"(?<!\S)site:\S+\s*", "", query).replace('"', '')
        relaxed = " ".join(relaxed.split())
        if relaxed and relaxed != query and relaxed not in searched and relaxed not in output:
            output.append(relaxed)
    return output[:3]


def unrelated_result(query, result):
    """Conservative lexical triage, not an eligibility or truth decision."""
    query = re.sub(r"-?site:\S+", "", query.casefold())
    terms = set(re.findall(r"[^\W_]{3,}", query)) - {
        "the", "and", "for", "with", "from", "find", "official", "website", "urls",
    }
    if len(terms) < 2 or not (result.get("title") or result.get("content")):
        return False
    text = " ".join(str(result.get(k, "")) for k in ("url", "title", "content")).casefold()
    words = set(re.findall(r"[^\W_]{3,}", text))
    return sum(any(w.startswith(t.rstrip("s")) for w in words) for t in terms) < 2
