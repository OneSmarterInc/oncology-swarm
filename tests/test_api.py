from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.cli import SEEDS
from app.worker import Coordinator

def test_token_required_and_no_persistent_browser_secret(stack):
    settings, db, service = stack
    with TestClient(create_app(settings, db)) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/api/status').status_code == 401
        assert client.get('/api/jobs', headers={'Authorization':'Bearer wrong'}).status_code == 401
        html = client.get('/')
        assert html.status_code == 200
        assert "frame-ancestors 'none'" in html.headers['content-security-policy']
        script = client.get('/static/app.js').text
        assert 'localStorage' not in script and 'sessionStorage' not in script
        assert 'innerHTML' not in script
        assert settings.admin_token not in html.text + script


def test_api_review_flow_pause_and_body_limits(stack):
    settings, db, service = stack
    headers={'Authorization':'Bearer '+settings.admin_token}
    with TestClient(create_app(settings, db), headers=headers) as client:
        result = client.post('/api/jobs', json={'urls':SEEDS}).json()
        id = result['results'][0]['id']
        assert client.post('/api/control', json={'paused':True}).json()['paused']
        assert client.get('/api/status').json()['paused']
        client.post('/api/control', json={'paused':False})
        Coordinator(service).run(once=True)
        j = client.get(f'/api/jobs/{id}').json()
        assert not j['proposal']['validation_errors']
        approved = client.post(f'/api/jobs/{id}/approve', json={'expected_version':1,'reviewer':'Test','note':'Reviewed source'})
        assert approved.status_code == 200, approved.text
        assert approved.json()['state'] == 'approved'
        assert client.get('/api/export.xlsx').content.startswith(b'PK')
        assert client.post(f'/api/jobs/{id}/approve', json={'expected_version':1,'reviewer':'Test','note':'Again'}).status_code == 409
        assert client.post('/api/jobs', content='a'*1000001, headers={'Content-Type':'application/json'}).status_code == 413


def test_placeholder_token_fails_startup(stack):
    settings, db, service = stack
    with pytest.raises(ValueError, match='ADMIN_TOKEN'):
        with TestClient(create_app(replace(settings, admin_token='REPLACE_'+'a'*40), db)):
            pass


def test_jobs_pagination_search_and_filters(stack):
    settings, db, service = stack
    headers = {'Authorization': 'Bearer ' + settings.admin_token}
    with TestClient(create_app(settings, db), headers=headers) as client:
        client.post('/api/jobs', json={'urls': SEEDS})
        missing = client.get('/api/jobs?validation=missing&limit=1').json()
        assert missing['total'] == 2 and len(missing['items']) == 1
        assert missing['offset'] == 0 and missing['limit'] == 1
        assert missing['items'][0]['version'] is None
        next_page = client.get('/api/jobs?offset=1&limit=1').json()
        assert next_page['items'][0]['id'] != missing['items'][0]['id']
        Coordinator(service).run(once=True)
        result = client.get('/api/jobs?q=asha&state=review&host=demo.example.invalid&validation=valid').json()
        assert result['total'] == 1
        job = result['items'][0]
        assert job['bulk_eligible'] and job['version'] == 1
        assert job['validation_error_count'] == 0 and job['duplicate_job_ids'] == []
        assert client.get('/api/jobs?host=example.invalid').json()['total'] == 0
        assert client.get('/api/jobs?q=%25').json()['total'] == 0  # Literal wildcard search.
        detail = client.get(f"/api/jobs/{job['id']}").json()
        data = detail['proposal']['data']
        data['evidence'] = []
        client.put(f"/api/jobs/{job['id']}/proposal", json={'data': data, 'expected_version': 1})
        invalid = client.get('/api/jobs?validation=invalid').json()
        assert invalid['total'] == 1 and not invalid['items'][0]['bulk_eligible']
        assert client.get('/api/jobs?validation=missing').json()['total'] == 0
        for query in ('limit=101', 'offset=-1', 'validation=unknown', 'host=https://example.invalid'):
            assert client.get('/api/jobs?' + query).status_code == 422


def test_selected_bulk_approval_requires_human_ack_and_version(stack):
    settings, db, service = stack
    with TestClient(create_app(settings, db), headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        ids = [item['id'] for item in client.post('/api/jobs', json={'urls': SEEDS}).json()['results']]
        Coordinator(service).run(once=True)
        body = {'reviewer': 'Reviewer', 'note': 'Checked every source and field', 'human_acknowledged': False,
                'items': [{'id': id, 'expected_version': 1} for id in ids]}
        assert client.post('/api/jobs/bulk-approve', json=body).status_code == 422
        assert client.get('/api/status').json()['approved_doctors'] == 0
        body['human_acknowledged'] = True
        duplicate_body = {**body, 'items': [body['items'][0], body['items'][0]]}
        assert client.post('/api/jobs/bulk-approve', json=duplicate_body).status_code == 422
        assert client.post('/api/jobs/bulk-approve', json={**body, 'items': body['items'] * 13}).status_code == 422
        # One stale version must not approve; the other selected version may succeed.
        body['items'][0]['expected_version'] = 99
        result = client.post('/api/jobs/bulk-approve', json=body)
        assert result.status_code == 200, result.text
        assert 'error' in result.json()['results'][0]
        assert result.json()['results'][1]['state'] == 'approved'
        assert client.get('/api/status').json()['approved_doctors'] == 1


def test_bulk_revalidates_instead_of_trusting_list_eligibility(stack):
    settings, db, service = stack
    with TestClient(create_app(settings, db), headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        id = client.post('/api/jobs', json={'urls': [SEEDS[0]]}).json()['results'][0]['id']
        Coordinator(service).run(once=True)
        data = client.get(f'/api/jobs/{id}').json()['proposal']['data']
        data['evidence'] = []
        assert client.put(f'/api/jobs/{id}/proposal', json={'data': data, 'expected_version': 1}).status_code == 200
        body = {'reviewer': 'Reviewer', 'note': 'Selected version', 'human_acknowledged': True,
                'items': [{'id': id, 'expected_version': 2}]}
        result = client.post('/api/jobs/bulk-approve', json=body).json()
        assert 'Validation blocked approval' in result['results'][0]['error']
        assert client.get('/api/status').json()['approved_doctors'] == 0


def test_correction_withdrawal_and_refresh_are_versioned(stack):
    settings, db, service = stack
    with TestClient(create_app(settings, db), headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        id = client.post('/api/jobs', json={'urls': [SEEDS[0]]}).json()['results'][0]['id']
        Coordinator(service).run(once=True)
        def action(name, version):
            return client.post(f'/api/jobs/{id}/{name}', json={'expected_version': version, 'reviewer': 'Reviewer', 'note': 'Verified evidence'})
        assert action('approve', 1).status_code == 200
        detail = client.get(f'/api/jobs/{id}').json()
        assert detail['doctor_id'] and detail['doctor_active']
        assert action('withdraw', 1).status_code == 409
        assert action('correct', 2).json()['state'] == 'review'
        assert client.get('/api/status').json()['approved_doctors'] == 0
        assert action('approve', 3).json()['state'] == 'approved'
        refreshed = action('refresh', 4)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()['generation'] == 1
        assert refreshed.json()['refresh_of_job_id'] == id
        assert action('refresh', 4).status_code == 409
        assert action('withdraw', 4).json()['state'] == 'withdrawn'
        assert client.get('/api/status').json()['approved_doctors'] == 0


def test_source_approval_and_bounded_discovery_routes(stack, monkeypatch):
    settings, db, service = stack
    settings = replace(settings, allowed_domains=('hospital.example',))
    app = create_app(settings, db)
    with TestClient(app, headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        body = {'name': 'Example Hospital', 'city': 'Chennai', 'hostname': 'hospital.example',
                'list_url': 'https://hospital.example/doctors', 'reviewer': 'Reviewer',
                'note': 'Approved for internal evidence-backed research', 'profile_url_pattern': '/doctors/'}
        source = client.post('/api/sources', json=body)
        assert source.status_code == 200, source.text
        id = source.json()['id']
        assert source.json()['source_type'] == 'official'
        assert client.get('/api/sources').json()['items'][0]['id'] == id
        assert client.post('/api/sources', json=body).status_code == 409
        assert client.post('/api/sources', json={**body, 'source_type': 'aggregator'}).status_code == 422
        assert client.post(f'/api/sources/{id}/discover', json={'max_pages': 51}).status_code == 422
        seen = []
        monkeypatch.setattr(app.state.service, 'discover_source', lambda source_id, **kwargs: seen.append((source_id, kwargs)) or {'urls': [], 'jobs': []})
        assert client.post(f'/api/sources/{id}/discover', json={'max_pages': 2, 'max_profiles': 8}).status_code == 200
        assert seen == [(id, {'max_pages': 2, 'max_profiles': 8, 'use_sitemap': False})]


def test_metrics_reset_and_all_new_routes_require_auth(stack):
    settings, db, service = stack
    app = create_app(settings, db)
    with TestClient(app) as client:
        for route in ('/api/metrics', '/api/sources'):
            assert client.get(route).status_code == 401
        for route in ('/api/jobs/bulk-approve', '/api/jobs/1/withdraw', '/api/jobs/1/correct', '/api/jobs/1/merge', '/api/jobs/1/refresh', '/api/control/reset-budgets', '/api/sources', '/api/sources/1/discover'):
            assert client.post(route, json={}).status_code == 401
        client.headers['Authorization'] = 'Bearer ' + settings.admin_token
        assert client.get('/api/metrics').status_code == 200
        body = {'reviewer': 'Reviewer', 'note': 'Approved new pilot run'}
        assert client.post('/api/control/reset-budgets', json=body).status_code == 409
        client.post('/api/control', json={'paused': True})
        result = client.post('/api/control/reset-budgets', json=body)
        assert result.status_code == 200, result.text
        assert result.json()['run_number'] == 2
        assert client.get('/api/status').json()['run_number'] == 2
        assert client.post('/api/control/reset-budgets', json={**body, 'reviewer': '  '}).status_code == 422


def test_bulk_rechecks_duplicates_and_merge_keeps_both_versions(stack, monkeypatch):
    from sqlalchemy import select
    from app.models import Proposal
    from app.service import WorkPaused
    settings, db, service = stack
    app = create_app(settings, db)
    with TestClient(app, headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        ids = [row['id'] for row in client.post('/api/jobs', json={'urls': SEEDS}).json()['results']]
        Coordinator(service).run(once=True)
        with db.session.begin() as session:
            proposals = session.scalars(select(Proposal).order_by(Proposal.job_id)).all()
            proposals[1].normalized_name = proposals[0].normalized_name
            proposals[1].data = {**proposals[1].data, 'doctor': {**proposals[1].data['doctor'], 'name': proposals[0].data['doctor']['name']}}
        result = client.post('/api/jobs/bulk-approve', json={'reviewer': 'Reviewer', 'note': 'Checked evidence',
            'human_acknowledged': True, 'items': [{'id': ids[0], 'expected_version': 1}]}).json()
        assert 'duplicate' in result['results'][0]['error'].lower()
        assert not client.get(f'/api/jobs?q=asha').json()['items'][0]['bulk_eligible']
        seen = []
        monkeypatch.setattr(app.state.service, 'merge', lambda *args: seen.append(args) or {'target_job_id': ids[1]})
        body = {'expected_version': 2, 'target_job_id': ids[1], 'target_expected_version': 7, 'reviewer': 'Reviewer', 'note': 'Compared both identities'}
        assert client.post(f'/api/jobs/{ids[0]}/merge', json=body).status_code == 200
        assert seen == [(ids[0], ids[1], 2, 7, 'Reviewer', 'Compared both identities')]
        assert client.post(f'/api/jobs/{ids[0]}/merge', json={**body, 'target_expected_version': 0}).status_code == 422
        def paused(*args, **kwargs):
            raise WorkPaused('Research is paused')
        monkeypatch.setattr(app.state.service, 'discover_source', paused)
        assert client.post('/api/sources/1/discover', json={}).status_code == 409


def test_malformed_proposal_remains_accessible_for_raw_correction(stack):
    settings, db, service = stack
    with TestClient(create_app(settings, db), headers={'Authorization': 'Bearer ' + settings.admin_token}) as client:
        id = client.post('/api/jobs', json={'urls': [SEEDS[0]]}).json()['results'][0]['id']
        Coordinator(service).run(once=True)
        bad_data = {'doctor': 'bad', 'unknown': 123}
        edited = client.put(f'/api/jobs/{id}/proposal', json={'data': bad_data, 'expected_version': 1})
        assert edited.status_code == 200 and edited.json()['validation_errors']
        listed = client.get('/api/jobs?validation=invalid')
        assert listed.status_code == 200
        assert listed.json()['items'][0]['name'] is None
        assert not listed.json()['items'][0]['bulk_eligible']
        assert client.get('/api/jobs?q=asha').status_code == 200
        detail = client.get(f'/api/jobs/{id}')
        assert detail.status_code == 200 and detail.json()['proposal']['data'] == bad_data
        assert client.post(f'/api/jobs/{id}/approve', json={'expected_version': 2, 'reviewer': 'Reviewer', 'note': 'Unsupported object'}).status_code == 409
