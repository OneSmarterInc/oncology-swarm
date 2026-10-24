from pathlib import Path
from types import SimpleNamespace
from oncoscraper.models import DoctorRecord
from oncoscraper.contacts import demote_shared
from oncoscraper.emails import find
from oncoscraper import batch
from oncoscraper.crawl import CrawlResult,PageLog
from oncoscraper.config import ScrapeConfig
from oncoscraper.sources import Source

def test_two_distinct_dnb_degrees_survive():
 r=DoctorRecord('https://hospital.test/doctor')
 r.set('qualifications',['DNB (Internal Medicine)','DNB (Medical Oncology)'],'labels')
 assert len(r.get('qualifications'))==2,r.values

def test_qualification_upgrade_has_evidence():
 r=DoctorRecord('https://hospital.test/doctor')
 r.set('qualifications',['MD'],'listing_card')
 r.set('qualifications',['MD (Medicine)'],'labels')
 assert any(e.value=='MD (Medicine)' for e in r.evidence),r.evidence

def test_qualification_upgrade_does_not_duplicate_later_in_same_list():
 r=DoctorRecord('https://hospital.test/doctor')
 r.set('qualifications',['MD'],'listing_card')
 r.set('qualifications',['MD (Medicine)','MD (Medicine)'],'labels')
 assert r.get('qualifications')==['MD (Medicine)']

def test_explicit_email_label_supports_plain_at():
 assert find('Email: asha at hospital.com')==['asha@hospital.com']

def test_same_doctor_two_affiliations_keeps_personal_email():
 rows=[]
 for h in ['Hospital A','Hospital B']:
  r=DoctorRecord('https://hospital.test/doctor');r.source_hospital=h
  r.set('name','Dr. Asha Sharma','labels');r.set('email','asha.sharma@hospital.test','labels');rows.append(r)
 demote_shared(rows)
 assert all(r.get('email') for r in rows),[r.values for r in rows]

def test_prose_not_converted_to_email():
 assert find('paper presented at APASICON-2015. Medical registration')==[]

def test_obfuscated_email_still_supported():
 assert find('asha [at] hospital [dot] com')==['asha@hospital.com']
 assert find('asha at hospital dot com')==['asha@hospital.com']

def test_different_doctors_shared_contact_is_demoted():
 rows=[]
 for name in ['Dr. Asha Sharma','Dr. Ram Singh']:
  r=DoctorRecord('https://hospital.test/doctor');r.set('name',name,'labels')
  r.set('email','desk@hospital.test','labels');rows.append(r)
 demote_shared(rows)
 assert all(not r.get('email') and r.get('contact_email') for r in rows)

def fake_batch(monkeypatch,tmp_path,stats):
 captured=[];calls=[]
 class Fake:
  def __init__(self,*args):pass
  def run(self,urls):
   calls.append(urls)
   return CrawlResult(urls,[],[PageLog(urls[0],200,'listing',0,0,'')],stats)
 monkeypatch.setattr(batch,'Crawler',Fake)
 def export(result,out,sites=None):captured.append((result,sites));return Path(out)
 monkeypatch.setattr(batch,'to_xlsx',export)
 return captured,calls

def test_budget_exhaustion_not_complete(monkeypatch,tmp_path):
 cap,calls=fake_batch(monkeypatch,tmp_path,{'stopped_early':False,'frontier_left':25,'pages_fetched':1})
 batch.run_batch([Source(2,'Test Hospital',['https://hospital.test/team'])],tmp_path/'audit.xlsx',ScrapeConfig(),1)
 assert cap[-1][1][0]['status']!='done',cap[-1][1]

def test_resume_changed_urls_fetches_again(monkeypatch,tmp_path):
 cap,calls=fake_batch(monkeypatch,tmp_path,{'stopped_early':False,'frontier_left':0})
 for url in ['https://hospital.test/old','https://hospital.test/new']:
  batch.run_batch([Source(2,'Test Hospital',[url])],tmp_path/'audit.xlsx',ScrapeConfig(),1)
 assert len(calls)==2,calls

def test_resume_unchanged_complete_source_is_cached(monkeypatch,tmp_path):
 cap,calls=fake_batch(monkeypatch,tmp_path,{'stopped_early':False,'frontier_left':0})
 for _ in range(2):
  batch.run_batch([Source(2,'Test Hospital',['https://hospital.test/team'])],tmp_path/'audit.xlsx',ScrapeConfig(),1)
 assert len(calls)==1
 assert cap[-1][1][0]['status']=='resumed'

def test_corrupt_resume_cache_recrawls(monkeypatch,tmp_path):
 cap,calls=fake_batch(monkeypatch,tmp_path,{'stopped_early':False,'frontier_left':0})
 sources=[Source(2,'Test Hospital',['https://hospital.test/team'])]
 batch.run_batch(sources,tmp_path/'audit.xlsx',ScrapeConfig(),1)
 next((tmp_path/'audit_sites').glob('*.json')).write_text('{broken')
 batch.run_batch(sources,tmp_path/'audit.xlsx',ScrapeConfig(),1)
 assert len(calls)==2
 assert cap[-1][1][0]['status']=='done'

def test_resume_larger_budget_reruns_partial_source(monkeypatch,tmp_path):
 cap,calls=fake_batch(monkeypatch,tmp_path,{'stopped_early':False,'frontier_left':25})
 for budget in [1,30]:
  batch.run_batch([Source(2,'Test Hospital',['https://hospital.test/team'])],tmp_path/'audit.xlsx',ScrapeConfig(),budget)
 assert len(calls)==2

def test_fresh_partial_attempt_invalidates_old_complete_cache(monkeypatch,tmp_path):
 stats={'stopped_early':False,'frontier_left':0}
 cap,calls=fake_batch(monkeypatch,tmp_path,stats)
 sources=[Source(2,'Test Hospital',['https://hospital.test/team'])]
 batch.run_batch(sources,tmp_path/'audit.xlsx',ScrapeConfig(),1)
 stats['frontier_left']=25
 batch.run_batch(sources,tmp_path/'audit.xlsx',ScrapeConfig(),1,resume=False)
 batch.run_batch(sources,tmp_path/'audit.xlsx',ScrapeConfig(),1)
 assert len(calls)==3
 assert cap[-1][1][0]['status']=='partial'

def test_partial_export_includes_active_pages(monkeypatch,tmp_path):
 cap=[]
 monkeypatch.setattr(batch,'to_xlsx',lambda result,out,sites=None:cap.append(result))
 crawler=SimpleNamespace(merger=SimpleNamespace(records=[]),value_pages={},result_pages=[PageLog('https://hospital.test/team',200,'listing',0,0,'')])
 src=Source(2,'Test Hospital',['https://hospital.test/team'])
 batch.snapshot_xlsx({'active':{2:(crawler,src)},'sources':[src]},tmp_path/'partial.xlsx')
 assert len(cap[0].pages)==1,cap[0].stats
