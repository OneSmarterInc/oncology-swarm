"""Bounded same-host discovery of oncology profile *candidates*.

This module has no database, service, browser, credential, or provider dependency.
Every network request goes through the supplied SafeFetcher, including sitemap
indexes, redirects and robots. Its durable reserve_request callback remains the
request-budget authority (and budget exceptions deliberately propagate).

Candidates are not verified doctors: enqueue them for extraction and human review.
JavaScript-only lists are reported as a coverage gap, not rendered with a second
transport that could bypass pinned DNS, robots, or the request budget.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import StringIO
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET

from .fetcher import FetchError, SafeFetcher, hidden_element


@dataclass(frozen=True)
class DiscoveredProfile:
    url: str
    found_on: str
    label: str
    oncology_evidence: str


@dataclass(frozen=True)
class DiscoveryIssue:
    url: str
    message: str
    retryable: bool = False


@dataclass
class DiscoveryResult:
    seed_url: str
    profiles: list[DiscoveredProfile] = field(default_factory=list)
    pages_fetched: list[str] = field(default_factory=list)
    sitemaps_fetched: list[str] = field(default_factory=list)
    errors: list[DiscoveryIssue] = field(default_factory=list)
    truncated: bool = False

    @property
    def urls(self) -> list[str]:
        return [profile.url for profile in self.profiles]


_ONCOLOGY = re.compile(r"\b(?:oncolog(?:y|ist|ists|ical)|cancer|(?:h(?:a)?emato|neuro|gyneco|gynaeco)oncolog(?:y|ist))\b", re.I)
_PROFILE_SEGMENTS = frozenset({"doctor", "doctors", "our-doctors", "consultant", "consultants", "expert", "experts",
                               "specialist", "specialists", "physician", "physicians", "oncologist", "oncologists",
                               "profile", "profiles", "find-a-doctor"})
_LIST_SEGMENTS = frozenset({"oncology", "medical-oncology", "surgical-oncology", "radiation-oncology", "cancer",
                           "all", "search", "list", "index", "page", "find", "specialities", "specialties",
                           "book", "book-appointment", "appointment", "appointments", "contact", "login", "register"})
_PAGE_KEYS = frozenset({"page", "paged", "pagenum", "page_no", "pageno", "page_number", "offset", "cursor"})
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_EXCLUDE = frozenset({"nav", "header", "footer"})
_MAX_NODES = 20_000
_MAX_DEPTH = 100
_MAX_LINKS = 2_000
_MAX_SITEMAP_ENTRIES = 5_000


def _oncology(value: str) -> bool:
    return bool(_ONCOLOGY.search(unquote(value).replace("-", " ").replace("_", " ")))


def _profile_candidate(url: str, label: str, prefix: str | None) -> bool:
    parts = urlsplit(url)
    path = unquote(parts.path).lower().rstrip("/")
    if path.endswith((".pdf", ".jpg", ".png", ".webp", ".xml", ".gz", ".js", ".css")):
        return False
    if prefix is not None:
        clean_prefix = prefix.lower().rstrip("/")
        return path.startswith(clean_prefix + "/") and path != clean_prefix
    segments = path.split("/")[1:]
    if not segments or segments[-1] in _LIST_SEGMENTS or re.fullmatch(r"(?:page-?)?\d+", segments[-1]):
        return False
    if any(re.match(r"dr[.-]", segment) for segment in segments):
        return True
    if any(segment in _PROFILE_SEGMENTS for segment in segments[:-1]):
        return True
    return bool(re.search(r"\b(?:Dr\.?|Doctor)\s+[A-Z][A-Za-z'-]+", label))


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    parent: int | None
    hidden: bool
    children: list[int | str] = field(default_factory=list)
    text: str = ""
    profile_urls: set[str] = field(default_factory=set)


class _ListingParser(HTMLParser):
    """Small bounded HTML tree for local card text, never page-wide specialty bleed."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = [_Node("root", {}, None, False)]
        self.stack = [0]
        self.links: list[int] = []
        self.truncated = False

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= _MAX_NODES or len(self.stack) >= _MAX_DEPTH:
            raise FetchError("Listing HTML exceeds the node or nesting limit")
        attr_map = dict(attrs)
        parent = self.stack[-1]
        hidden = self.nodes[parent].hidden or hidden_element(tag, attr_map) or tag in _EXCLUDE
        index = len(self.nodes)
        self.nodes.append(_Node(tag, attr_map, parent, hidden))
        self.nodes[parent].children.append(index)
        head_next = (tag == "link" and "next" in (attr_map.get("rel") or "").lower().split()
                     and self.nodes[parent].tag == "head")
        if attr_map.get("href") and ((tag == "a" and not hidden) or head_next):
            if len(self.links) < _MAX_LINKS:
                self.links.append(index)
            else:
                self.truncated = True
        if tag not in _VOID:
            self.stack.append(index)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, 0, -1):
            if self.nodes[self.stack[position]].tag == tag:
                del self.stack[position:]
                break

    def handle_data(self, data):
        if not self.nodes[self.stack[-1]].hidden:
            self.nodes[self.stack[-1]].children.append(data)

    def finish(self):
        self.close()
        for node in reversed(self.nodes):
            if node.hidden:
                continue
            # Truncate local snippets while building them; do not duplicate the
            # whole page's text into every ancestor on deep hostile documents.
            parts, count = [], 0
            for child in node.children:
                value = child if isinstance(child, str) else self.nodes[child].text
                if value:
                    parts.append(value[:2001 - count])
                    count += len(parts[-1]) + 1
                if count > 2000:
                    break
            node.text = " ".join(" ".join(parts).split())[:2000]

    def mark_profile(self, index: int, url: str):
        while index is not None:
            node = self.nodes[index]
            if len(node.profile_urls) < 2:
                node.profile_urls.add(url)
            index = node.parent

    def local_context(self, index: int) -> str:
        selected = self.nodes[index].text
        for _ in range(6):
            index = self.nodes[index].parent
            if index is None:
                break
            node = self.nodes[index]
            if node.tag in {"root", "body", "html", "main"} or len(node.profile_urls) > 1:
                break
            if node.text:
                selected = node.text
        return selected


def _pagination(url: str, current: str, label: str, rel: str) -> bool:
    parts, base = urlsplit(url), urlsplit(current)
    query, base_query = parse_qs(parts.query), parse_qs(base.query)
    page_changes = any(key.lower() in _PAGE_KEYS and values != base_query.get(key) for key, values in query.items())
    normalize_path = lambda path: re.sub(r"/page/\d+/?$", "", path.rstrip("/"))
    same_listing = normalize_path(parts.path) == normalize_path(base.path)
    page_path = parts.path != base.path and bool(re.search(r"/page/\d+/?$", parts.path))
    explicit = "next" in rel.lower().split() or label.strip().lower() in {"next", "next page", "older", "›", "»", ">"}
    return same_listing and (page_changes or page_path or explicit) and url != current


def _sitemap_entries(xml: str) -> tuple[str, list[str], bool]:
    # ElementTree never resolves external entities, but rejecting declarations
    # also prevents internal entity expansion before parsing and keeps XML inert.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
        raise FetchError("Sitemap DTD and entity declarations are forbidden")
    entries: list[str] = []
    stack: list[str] = []
    root = ""
    nodes = 0
    try:
        for event, element in ET.iterparse(StringIO(xml), events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start":
                stack.append(tag)
                nodes += 1
                if nodes > _MAX_NODES or len(stack) > 32:
                    raise FetchError("Sitemap exceeds the XML node or nesting limit")
                if not root:
                    root = tag
                    if root not in {"urlset", "sitemapindex"}:
                        raise FetchError("Sitemap root must be urlset or sitemapindex")
            else:
                if len(stack) == 3 and tag == "loc" and stack[1] == ("url" if root == "urlset" else "sitemap"):
                    value = (element.text or "").strip()
                    if value:
                        if len(entries) >= _MAX_SITEMAP_ENTRIES:
                            return root, entries, True
                        entries.append(value)
                stack.pop()
                element.clear()
    except ET.ParseError as exc:
        raise FetchError("Malformed sitemap XML") from exc
    return root, entries, False


def discover(fetcher: SafeFetcher, seed_url: str, *, max_pages: int = 10, max_profiles: int = 100,
             max_sitemaps: int = 3, include_sitemap: bool = True, sitemap_url: str | None = None,
             profile_path_prefix: str | None = None) -> DiscoveryResult:
    """Discover oncology candidates with deterministic limits and no side effects.

    ``max_pages`` and ``max_sitemaps`` count attempted documents, including failed
    fetches. All robots and redirect hops consume SafeFetcher's durable request
    budget. The maximum possible network calls is bounded further by its redirect
    cap. Exact-host restrictions also apply to redirected targets before I/O.

    ``profile_path_prefix`` is a literal path such as ``/specialists``; it replaces
    the default profile-path heuristic, but never relaxes the oncology filter.
    Explicit sitemap URLs are allowed only on the seed's exact HTTPS hostname.
    Budget/pause exceptions from reserve_request are intentionally not swallowed.
    """
    for name, value, lower, upper in (("max_pages", max_pages, 1, 100), ("max_profiles", max_profiles, 1, 10000),
                                      ("max_sitemaps", max_sitemaps, 0, 20)):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f"{name} must be between {lower} and {upper}")
    if profile_path_prefix is not None:
        if (not isinstance(profile_path_prefix, str) or not profile_path_prefix.startswith("/")
                or profile_path_prefix.startswith("//") or len(profile_path_prefix) > 500
                or any(c in profile_path_prefix for c in "?#\\")
                or any(ord(c) <= 32 for c in profile_path_prefix)):
            raise ValueError("profile_path_prefix must be a bounded literal URL path")
    seed = fetcher.normalize_url(seed_url)
    host = urlsplit(seed).hostname
    result = DiscoveryResult(seed)
    pending = deque([seed])
    queued = {seed}
    attempted: set[str] = set()
    page_attempts = 0
    seen_profiles: set[str] = set()

    def safe_link(base: str, href: str) -> str | None:
        try:
            return fetcher.normalize_url(urljoin(base, href), same_host=host)
        except (FetchError, ValueError):
            return None

    def add_profile(url: str, found_on: str, label: str, evidence: str):
        if url in seen_profiles:
            return
        if len(result.profiles) >= max_profiles:
            result.truncated = True
            return
        seen_profiles.add(url)
        result.profiles.append(DiscoveredProfile(url, found_on, label[:250], evidence[:500]))

    while pending and page_attempts < max_pages and len(result.profiles) < max_profiles:
        url = pending.popleft()
        if url in attempted:
            continue
        attempted.add(url)
        page_attempts += 1
        try:
            page = fetcher.fetch(url, same_host=host)
            result.pages_fetched.append(page.url)
            attempted.add(page.url)  # Do not refetch an already-visited redirect target.
            parser = _ListingParser()
            parser.feed(page.html)
            parser.finish()
            result.truncated |= parser.truncated
            links = []
            for index in parser.links:
                node = parser.nodes[index]
                target = safe_link(page.url, node.attrs["href"] or "")
                if target is None or target == page.url:
                    continue
                candidate = _profile_candidate(target, node.text, profile_path_prefix)
                if candidate:
                    parser.mark_profile(index, target)
                links.append((index, target, candidate))
            for index, target, candidate in links:
                node = parser.nodes[index]
                context = parser.local_context(index)
                if candidate:
                    evidence = context if _oncology(context) else target if _oncology(target) else ""
                    if not evidence and _oncology(page.url):
                        evidence = f"Oncology listing URL: {page.url}"
                    if evidence:
                        add_profile(target, page.url, node.text, evidence)
                elif _pagination(target, page.url, node.text, node.attrs.get("rel") or "") and target not in queued:
                    # The pending queue, not just fetched documents, is bounded.
                    if len(queued) < max_pages:
                        pending.append(target)
                        queued.add(target)
                    else:
                        result.truncated = True
            if not parser.links:
                result.errors.append(DiscoveryIssue(page.url, "No visible profile links found; the list may require JavaScript or a site-specific adapter"))
        except FetchError as exc:
            result.errors.append(DiscoveryIssue(url, str(exc), exc.retryable))
    result.truncated |= bool(pending)

    # A URL naming a sitemap explicitly must not silently become an unsafe
    # default fetch on another host. Validate it even when the profile cap is hit.
    sitemap_seed = sitemap_url if sitemap_url is not None else f"https://{host}/sitemap.xml"
    if include_sitemap and max_sitemaps:
        sitemap_seed = fetcher.normalize_url(sitemap_seed, same_host=host)
        sitemap_pending = deque([sitemap_seed])
        sitemap_queued = {sitemap_seed}
        sitemap_attempts = 0
        while sitemap_pending and sitemap_attempts < max_sitemaps and len(result.profiles) < max_profiles:
            url = sitemap_pending.popleft()
            sitemap_attempts += 1
            try:
                document = fetcher.fetch_sitemap(url, same_host=host)
                result.sitemaps_fetched.append(document.url)
                root, entries, truncated = _sitemap_entries(document.text)
                result.truncated |= truncated
                for value in entries:
                    target = safe_link(document.url, value)
                    if target is None:
                        continue
                    if root == "sitemapindex":
                        if target not in sitemap_queued:
                            if len(sitemap_queued) < max_sitemaps:
                                sitemap_pending.append(target)
                                sitemap_queued.add(target)
                            else:
                                result.truncated = True
                    elif _profile_candidate(target, "", profile_path_prefix) and _oncology(target):
                        add_profile(target, document.url, "", target)
            except FetchError as exc:
                result.errors.append(DiscoveryIssue(url, str(exc), exc.retryable))
        result.truncated |= bool(sitemap_pending)
    return result
