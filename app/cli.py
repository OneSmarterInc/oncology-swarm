import argparse
import logging
import json
from pathlib import Path
import secrets
import signal
from app.config import get_settings
from app.db import Database
from app.service import Service
from app.export import export_xlsx

SEEDS = ['https://demo.example.invalid/doctors/asha-rao', 'https://demo.example.invalid/doctors/vikram-sen']

def main():
    p = argparse.ArgumentParser(description='Evidence-first oncology research starter')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'demo', 'worker', 'run-once', 'token', 'metrics'):
        sub.add_parser(name)
    export = sub.add_parser('export'); export.add_argument('--output', required=True)
    args = p.parse_args()
    if args.command == 'token':
        print(secrets.token_urlsafe(36)); return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = get_settings(); db = Database(settings.database_url)
    if args.command == 'init':
        db.init_demo(); print('SQLite demo schema initialized. PostgreSQL: use alembic upgrade head.'); return
    db.ensure_control(); service = Service(db, settings)
    if args.command == 'demo':
        if settings.mode != 'fixture': p.error('demo requires MODE=fixture')
        for url in SEEDS:
            job_id, created = service.enqueue(url); print(f'{job_id}: {"queued" if created else "already exists"} {url}')
    elif args.command == 'metrics':
        print(json.dumps(service.metrics(), indent=2, default=str))
    elif args.command == 'export':
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(export_xlsx(db)); print(f'Exported human-approved records to {path}')
    else:
        from app.worker import Coordinator
        coordinator = Coordinator(service)
        def stop(*_): coordinator.stop_event.set()
        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, 'SIGTERM'): signal.signal(signal.SIGTERM, stop)
        coordinator.run(once=args.command == 'run-once')

if __name__ == '__main__': main()
