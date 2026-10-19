"""Directory sources, reviewed lifecycle, indexed identity and budget runs.

Revision ID: b592cf2d0101
Revises: e271a68cbbf7
"""
from alembic import op
import sqlalchemy as sa
from urllib.parse import urlsplit
import re
revision='b592cf2d0101'
down_revision='e271a68cbbf7'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('hospitals',sa.Column('id',sa.Integer,primary_key=True),sa.Column('name',sa.String(500),nullable=False),sa.Column('normalized_name',sa.String(500),nullable=False),sa.Column('city',sa.String(300)))
    op.create_index('ix_hospitals_normalized_name','hospitals',['normalized_name'])
    op.create_table('sources',sa.Column('id',sa.Integer,primary_key=True),sa.Column('hospital_id',sa.Integer,sa.ForeignKey('hospitals.id'),nullable=False),sa.Column('hostname',sa.String(253),nullable=False),sa.Column('list_url',sa.String(2048),nullable=False,unique=True),sa.Column('sitemap_url',sa.String(2048)),sa.Column('profile_url_pattern',sa.String(300)),sa.Column('source_type',sa.String(32),nullable=False),sa.Column('approved_at',sa.DateTime,nullable=False),sa.Column('reviewer',sa.String(100),nullable=False),sa.Column('note',sa.Text,nullable=False),sa.Column('active',sa.Boolean,nullable=False))
    op.create_index('ix_sources_hostname','sources',['hostname'])
    op.add_column('control',sa.Column('run_number',sa.Integer,nullable=False,server_default='1'))
    # Naming convention gives the original unnamed SQLite unique constraint a stable name.
    with op.batch_alter_table('jobs',naming_convention={'uq':'uq_%(table_name)s_%(column_0_name)s'}) as b:
        b.drop_constraint('uq_jobs_url' if op.get_bind().dialect.name=='sqlite' else 'jobs_url_key',type_='unique')
        b.add_column(sa.Column('llm_call_sequence',sa.Integer,nullable=False,server_default='0'))
        b.add_column(sa.Column('host',sa.String(253),nullable=False,server_default=''))
        b.add_column(sa.Column('generation',sa.Integer,nullable=False,server_default='0'))
        b.add_column(sa.Column('source_id',sa.Integer,nullable=True))
        b.add_column(sa.Column('refresh_of_job_id',sa.Integer,nullable=True))
        b.add_column(sa.Column('run_number',sa.Integer,nullable=False,server_default='1'))
        b.create_foreign_key('fk_jobs_source','sources',['source_id'],['id'])
        b.create_foreign_key('fk_jobs_refresh','jobs',['refresh_of_job_id'],['id'])
        b.create_unique_constraint('uq_jobs_url_generation',['url','generation'])
        b.create_index('ix_jobs_host',['host'])
    with op.batch_alter_table('snapshots') as b:
        b.add_column(sa.Column('source_id',sa.Integer,nullable=True))
        b.add_column(sa.Column('source_type',sa.String(32),nullable=False,server_default='unclassified'))
        b.create_foreign_key('fk_snapshot_source','sources',['source_id'],['id'])
    op.add_column('proposals',sa.Column('normalized_name',sa.String(300),nullable=False,server_default=''))
    op.create_index('ix_proposals_normalized_name','proposals',['normalized_name'])
    with op.batch_alter_table('doctors') as b:
        b.add_column(sa.Column('active',sa.Boolean,nullable=False,server_default=sa.true()))
        b.add_column(sa.Column('needs_review',sa.Boolean,nullable=False,server_default=sa.false()))
        b.add_column(sa.Column('source_id',sa.Integer,nullable=True))
        b.add_column(sa.Column('source_type',sa.String(32),nullable=False,server_default='unclassified'))
        for name in ('qualifications','experience_years','sub_specialty','languages','opd_timings','consultation_fee'):
            b.add_column(sa.Column(name,sa.String(1000)))
        b.add_column(sa.Column('profile_photo_url',sa.String(2048)))
        b.create_foreign_key('fk_doctor_source','sources',['source_id'],['id'])
        b.create_index('ix_doctors_active',['active'])
    with op.batch_alter_table('affiliations') as b:
        b.add_column(sa.Column('hospital_id',sa.Integer,nullable=True))
        b.create_foreign_key('fk_affiliation_hospital','hospitals',['hospital_id'],['id'])
    for table in ('affiliations','contacts','registrations'):
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column('source_proposal_id',sa.Integer,nullable=True))
            b.create_foreign_key('fk_'+table+'_proposal','proposals',['source_proposal_id'],['id'])
    op.create_table('doctor_sources',sa.Column('id',sa.Integer,primary_key=True),sa.Column('doctor_id',sa.Integer,sa.ForeignKey('doctors.id'),nullable=False),sa.Column('proposal_id',sa.Integer,sa.ForeignKey('proposals.id'),nullable=False),sa.UniqueConstraint('doctor_id','proposal_id',name='uq_doctor_sources'))
    op.create_index('ix_doctor_sources_doctor_id','doctor_sources',['doctor_id'])
    op.create_table('llm_usage',sa.Column('id',sa.Integer,primary_key=True),sa.Column('job_id',sa.Integer,sa.ForeignKey('jobs.id'),nullable=False),sa.Column('attempt',sa.Integer,nullable=False),sa.Column('run_number',sa.Integer,nullable=False),sa.Column('provider',sa.String(50),nullable=False),sa.Column('model',sa.String(200),nullable=False),sa.Column('input_tokens',sa.Integer),sa.Column('output_tokens',sa.Integer),sa.Column('total_tokens',sa.Integer),sa.Column('estimated_cost_usd',sa.Float),sa.Column('created_at',sa.DateTime,nullable=False),sa.UniqueConstraint('job_id','attempt',name='uq_llm_usage_attempt'))
    op.create_index('ix_llm_usage_job_id','llm_usage',['job_id'])
    op.create_table('host_pacing',sa.Column('hostname',sa.String(253),primary_key=True),sa.Column('next_request_at',sa.DateTime,nullable=False),sa.Column('last_request_at',sa.DateTime,nullable=False),sa.Column('interval_seconds',sa.Float,nullable=False))
    op.create_table('discovery_runs',sa.Column('id',sa.Integer,primary_key=True),sa.Column('source_id',sa.Integer,sa.ForeignKey('sources.id'),nullable=False),sa.Column('state',sa.String(24),nullable=False),sa.Column('owner',sa.String(100),nullable=False),sa.Column('lease_expires_at',sa.DateTime,nullable=False),sa.Column('created_at',sa.DateTime,nullable=False),sa.Column('updated_at',sa.DateTime,nullable=False),sa.Column('summary',sa.JSON,nullable=False))
    op.create_index('ix_discovery_runs_state','discovery_runs',['state'])
    connection=op.get_bind()
    jobs=sa.table('jobs',sa.column('id',sa.Integer),sa.column('url',sa.String),sa.column('host',sa.String))
    for id,url in connection.execute(sa.select(jobs.c.id,jobs.c.url)):
        connection.execute(jobs.update().where(jobs.c.id==id).values(host=urlsplit(url).hostname or ''))
    proposals=sa.table('proposals',sa.column('id',sa.Integer),sa.column('data',sa.JSON),sa.column('normalized_name',sa.String))
    for id,data in connection.execute(sa.select(proposals.c.id,proposals.c.data)):
        doctor=data.get('doctor') if isinstance(data,dict) else None
        name=doctor.get('name','') if isinstance(doctor,dict) else ''
        name=re.sub(r'^(?:dr\.?|doctor|prof\.?)\s+','',name.strip(),flags=re.I) if isinstance(name,str) else ''
        connection.execute(proposals.update().where(proposals.c.id==id).values(normalized_name=re.sub(r'[^\w]+',' ',name.casefold()).strip()))
    for table in ('affiliations','contacts','registrations'):
        connection.execute(sa.text(f'UPDATE {table} SET source_proposal_id=(SELECT proposal_id FROM doctors WHERE doctors.id={table}.doctor_id)'))
    connection.execute(sa.text("INSERT INTO doctor_sources (doctor_id, proposal_id) SELECT id, proposal_id FROM doctors"))
    connection.execute(sa.text("UPDATE doctors SET source_type='synthetic' WHERE synthetic=true"))
    connection.execute(sa.text("UPDATE snapshots SET source_type='synthetic' WHERE synthetic=true"))

def downgrade():
    raise RuntimeError('V2 migration is forward-only to preserve refresh generations and review audit; restore a pre-upgrade backup instead')
