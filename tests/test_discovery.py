from types import SimpleNamespace
import socket
from unittest.mock import Mock

import pytest

from app.discovery import discover, _sitemap_entries
from app.fetcher import FetchError, SafeFetcher, _Response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_a, **_kw):
        raise AssertionError('Discovery unit tests must never access the network')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


class OfflineFetcher(SafeFetcher):
    """Exercise real robots, redirects and URL policy with an inert transport."""
    def __init__(self, documents, robots='User-agent: *\nAllow: /\n'):
        settings = SimpleNamespace(allowed_domains=('hospital.example', 'other.example'),
            requests_per_minute=30, request_timeout_seconds=5, max_response_bytes=100000,
            max_redirects=3, user_agent='OncologyResearch/2.0 (+https://example.com/contact)', mode='live')
        self.reservations = []
        super().__init__(settings, self.reservations.append)
        self.documents = documents
        self.robots_body = robots

    def _request_once(self, target):
        self.reserve_request(target.url)
        if target.path == '/robots.txt':
            return _Response(200, {'content-type': 'text/plain'}, self.robots_body.encode())
        value = self.documents.get(target.url, self.documents.get(target.path))
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, _Response):
            return value
        if value is None:
            return _Response(404, {'content-type': 'text/plain'}, b'Not found')
        media = 'application/xml' if target.path.endswith(('.xml', '.xml.gz')) else 'text/html'
        return _Response(200, {'content-type': media}, value.encode())


def card(name='Asha', specialty='Medical oncology', path='/doctors/asha'):
    return f'<article><h2><a href="{path}">Dr {name}</a></h2><p>{specialty}</p></article>'


def sitemap(urls, index=False):
    root, child = ('sitemapindex', 'sitemap') if index else ('urlset', 'url')
    return f'<{root} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(
        f'<{child}><loc>{url}</loc></{child}>' for url in urls) + f'</{root}>'


def test_discovers_local_card_specialty_without_cross_doctor_bleed():
    fetcher = OfflineFetcher({'/doctors': '<main>' + card() + card('Bala', 'Cardiology', '/doctors/bala') + '</main>'})
    result = discover(fetcher, 'https://hospital.example/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha']
    assert result.profiles[0].label == 'Dr Asha'
    assert result.profiles[0].oncology_evidence == 'Dr Asha Medical oncology'
    assert result.profiles[0].found_on == 'https://hospital.example/doctors'
    assert fetcher.reservations == ['https://hospital.example/robots.txt', 'https://hospital.example/doctors']


def test_duplicate_card_anchors_keep_specialty_context():
    html = '<article><a href="/doctors/asha">Dr Asha</a><p>Oncologist</p><a href="/doctors/asha">View profile</a></article>'
    result = discover(OfflineFetcher({'/doctors': html}), 'https://hospital.example/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha']


def test_cross_host_nonhttps_hidden_navigation_and_injected_links_ignored():
    html = card() + ''.join(card('Unsafe', path=url) for url in [
        'https://other.example/doctors/unsafe', 'https://127.0.0.1/doctors/unsafe',
        'https://hospital.example.attacker.example/doctors/unsafe', 'http://hospital.example/doctors/unsafe',
        'javascript:alert(1)', 'https://u:password@hospital.example/doctors/unsafe'])
    html += '<nav>' + card('Nav', path='/doctors/nav') + '</nav>'
    html += '<div hidden>' + card('Hidden', path='/doctors/hidden') + '</div>'
    html += '<script><a href="/doctors/script">Dr Cancer</a></script>'
    fetcher = OfflineFetcher({'/doctors': html})
    result = discover(fetcher, 'https://hospital.example/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha']
    assert len(fetcher.reservations) == 2


def test_relative_urls_fragments_and_case_deduplicated():
    html = card(path='../doctors/asha#bio') + card(path='https://HOSPITAL.example:443/doctors/asha#cv')
    result = discover(OfflineFetcher({'/list/doctors': html}), 'https://hospital.example/list/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha']


@pytest.mark.parametrize('next_link', ['<a rel="next" href="?page=2">More</a>', '<a href="?page=2">2</a>',
                                       '<head><link rel="next" href="?page=2" /></head>'])
def test_pagination_links_and_rel_next(next_link):
    fetcher = OfflineFetcher({'/doctors': card() + next_link,
        '/doctors?page=2': card('Bala', 'Surgical oncology', '/doctors/bala') + '<a href="/doctors">Back</a>'})
    result = discover(fetcher, 'https://hospital.example/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha', 'https://hospital.example/doctors/bala']
    assert result.pages_fetched == ['https://hospital.example/doctors', 'https://hospital.example/doctors?page=2']
    assert not result.truncated


def test_pagination_path_and_attempt_limits():
    fetcher = OfflineFetcher({'/doctors': card() + '<a rel="next" href="/doctors/page/2">Next</a>',
        '/doctors/page/2': card('Bala', 'Surgical oncology', '/doctors/bala') + '<a rel="next" href="/doctors/page/3">Next</a>'})
    result = discover(fetcher, 'https://hospital.example/doctors', max_pages=2, include_sitemap=False)
    assert len(result.urls) == 2 and len(result.pages_fetched) == 2 and result.truncated
    assert len(fetcher.reservations) == 3


def test_profile_limit_and_pending_queue_bounded():
    html = card() + card('Bala', 'Surgical oncology', '/doctors/bala')
    html += ''.join(f'<a href="?page={i}">{i}</a>' for i in range(2, 100))
    fetcher = OfflineFetcher({'/doctors': html})
    result = discover(fetcher, 'https://hospital.example/doctors', max_profiles=1, max_pages=2)
    assert len(result.urls) == 1 and result.truncated
    assert len(fetcher.reservations) == 2


def test_prefix_is_literal_and_still_requires_oncology():
    html = card(path='/team/asha') + card('Bala', 'Cardiology', '/team/bala')
    html += card('Other', path='/doctors/other')
    result = discover(OfflineFetcher({'/directory': html}), 'https://hospital.example/directory',
                      profile_path_prefix='/team', include_sitemap=False)
    assert result.urls == ['https://hospital.example/team/asha']


def test_oncology_listing_and_url_patterns_find_candidates():
    html = '<article><a href="/doctors/asha">Dr Asha</a></article><a href="/services/oncology">Cancer care</a>'
    result = discover(OfflineFetcher({'/oncology/doctors': html}), 'https://hospital.example/oncology/doctors', include_sitemap=False)
    assert result.urls == ['https://hospital.example/doctors/asha']
    assert result.profiles[0].oncology_evidence.startswith('Oncology listing URL:')


def test_sitemap_index_same_host_filters_oncology_and_deduplicates():
    fetcher = OfflineFetcher({'/doctors': card(path='/doctors/asha-oncologist'),
        '/sitemap.xml': sitemap(['https://hospital.example/doctors.xml', 'https://other.example/foreign.xml'], index=True),
        '/doctors.xml': sitemap(['https://hospital.example/doctors/asha-oncologist',
            'https://hospital.example/doctors/bala-cancer-specialist', 'https://hospital.example/doctors/cardiologist',
            'https://hospital.example/services/oncology', 'http://hospital.example/doctors/oncologist-unsafe'])})
    result = discover(fetcher, 'https://hospital.example/doctors')
    assert result.urls == ['https://hospital.example/doctors/asha-oncologist', 'https://hospital.example/doctors/bala-cancer-specialist']
    assert result.sitemaps_fetched == ['https://hospital.example/sitemap.xml', 'https://hospital.example/doctors.xml']
    assert len(fetcher.reservations) == 4


def test_explicit_sitemap_and_max_sitemap_attempts():
    fetcher = OfflineFetcher({'/doctors': card(), '/custom.xml': sitemap(['https://hospital.example/a.xml',
        'https://hospital.example/b.xml', 'https://hospital.example/c.xml'], index=True),
        '/a.xml': FetchError('Sitemap unavailable', retryable=True)})
    result = discover(fetcher, 'https://hospital.example/doctors', sitemap_url='https://hospital.example/custom.xml', max_sitemaps=2)
    assert result.sitemaps_fetched == ['https://hospital.example/custom.xml']
    assert result.truncated and result.errors[0].retryable
    assert len(fetcher.reservations) == 4


def test_sitemap_entity_declarations_and_bad_roots_refused():
    for document in ['<!DOCTYPE x [<!ENTITY boom "hello">]><urlset>&boom;</urlset>', '<html>Not a sitemap</html>', '<urlset>']:
        with pytest.raises(FetchError):
            _sitemap_entries(document)


def test_sitemap_does_not_follow_image_location_elements():
    xml = '<urlset xmlns:image="https://example.com/image"><url><loc>https://hospital.example/doctors/oncologist</loc>'
    xml += '<image:image><image:loc>https://hospital.example/doctors/cancer-evil</image:loc></image:image></url></urlset>'
    root, urls, truncated = _sitemap_entries(xml)
    assert root == 'urlset' and urls == ['https://hospital.example/doctors/oncologist'] and not truncated


def test_robots_blocks_list_and_sitemap_before_document_calls():
    fetcher = OfflineFetcher({}, robots='User-agent: *\nDisallow: /\n')
    result = discover(fetcher, 'https://hospital.example/doctors')
    assert result.urls == [] and result.pages_fetched == [] and result.sitemaps_fetched == []
    assert len(result.errors) == 2
    assert fetcher.reservations == ['https://hospital.example/robots.txt']


def test_cross_origin_redirect_never_fetches_even_allowlisted_host():
    fetcher = OfflineFetcher({'/doctors': _Response(302, {'location': 'https://other.example/doctors'}, b'')})
    result = discover(fetcher, 'https://hospital.example/doctors', include_sitemap=False)
    assert not result.urls and 'Cross-origin' in result.errors[0].message
    assert len(fetcher.reservations) == 2


def test_budget_exhaustion_propagates_instead_of_reporting_complete():
    class BudgetExhausted(Exception):
        pass
    fetcher = OfflineFetcher({'/doctors': card()})
    def reserve(_url):
        raise BudgetExhausted('No budget')
    fetcher.reserve_request = reserve
    with pytest.raises(BudgetExhausted):
        discover(fetcher, 'https://hospital.example/doctors')


def test_javascript_only_page_has_honest_coverage_issue():
    fetcher = OfflineFetcher({'/doctors': '<div id="app"></div><script>renderDoctors()</script>'})
    result = discover(fetcher, 'https://hospital.example/doctors', include_sitemap=False)
    assert not result.urls and 'JavaScript' in result.errors[0].message


def test_deep_html_is_bounded_and_reported():
    result = discover(OfflineFetcher({'/doctors': '<div>' * 101}), 'https://hospital.example/doctors', include_sitemap=False)
    assert result.urls == [] and 'nesting limit' in result.errors[0].message


@pytest.mark.parametrize('kwargs', [{'max_pages': 0}, {'max_pages': 101}, {'max_pages': True}, {'max_profiles': 0},
                                   {'max_profiles': 10001}, {'max_sitemaps': 21}, {'max_sitemaps': -1},
                                   {'profile_path_prefix': 'https://evil'}, {'profile_path_prefix': '//evil'},
                                   {'profile_path_prefix': '/doctors?evil'}, {'profile_path_prefix': '/a\nb'}])
def test_invalid_limits_rejected_before_any_fetch(kwargs):
    fetcher = OfflineFetcher({})
    with pytest.raises(ValueError):
        discover(fetcher, 'https://hospital.example/doctors', **kwargs)
    assert fetcher.reservations == []


def test_explicit_other_host_sitemap_refused():
    fetcher = OfflineFetcher({'/doctors': card()})
    with pytest.raises(FetchError, match='Cross-origin'):
        discover(fetcher, 'https://hospital.example/doctors', sitemap_url='https://other.example/sitemap.xml')
    assert all('other.example' not in url for url in fetcher.reservations)
