"""Deterministic evidence gates. These verify provenance, not medical truth."""
import re
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Identity(StrictModel):
    name: str = Field(max_length=300)
    specialty: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=300)
    qualifications: str | None = Field(default=None, max_length=1000)
    # Keep source semantics such as "20+ years"; never infer a start year.
    experience_years: str | None = Field(default=None, max_length=300)
    sub_specialty: str | None = Field(default=None, max_length=500)
    languages: str | None = Field(default=None, max_length=500)
    opd_timings: str | None = Field(default=None, max_length=1000)
    consultation_fee: str | None = Field(default=None, max_length=300)
    profile_photo_url: str | None = Field(default=None, max_length=2048)

class AffiliationData(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    city: str | None = Field(default=None, max_length=300)

class ContactData(StrictModel):
    type: Literal['email', 'phone']
    value: str = Field(min_length=1, max_length=500)
    scope: Literal['doctor', 'institution_central', 'unclassified']

class RegistrationData(StrictModel):
    number: str = Field(min_length=1, max_length=300)
    authority: str | None = Field(default=None, max_length=300)
    status: Literal['provider_claimed', 'historical_record', 'current_independently_verified']

class EvidenceData(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    field: str = Field(min_length=1, max_length=300)
    value: str = Field(max_length=2048)
    source_url: str = Field(max_length=2048)
    support_text: str = Field(min_length=1, max_length=4000)
    retrieved_at: str = Field(max_length=40)

class ProposalData(StrictModel):
    schema_version: Literal[1]
    synthetic: bool
    doctor: Identity
    affiliations: list[AffiliationData] = Field(max_length=30)
    contacts: list[ContactData] = Field(max_length=50)
    registration: list[RegistrationData] = Field(max_length=20)
    evidence: list[EvidenceData] = Field(max_length=300)
    warnings: list[str] = Field(max_length=100)

def normalize_name(name):
    text = re.sub(r'^(?:dr\.?|doctor|prof\.?)\s+', '', name.strip(), flags=re.I)
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()

def fact_values(data):
    result = {}
    for key, value in data['doctor'].items():
        if value:
            result[f'doctor.{key}'] = value
    for kind in ('affiliations', 'contacts', 'registration'):
        for i, record in enumerate(data[kind]):
            for key, value in record.items():
                if value and key not in {'type', 'scope', 'status'}:
                    result[f'{kind}.{i}.{key}'] = value
    return result

def validate_proposal(data, snapshot):
    errors = []
    try:
        parsed = ProposalData.model_validate(data).model_dump()
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    if not parsed['doctor']['name'].strip():
        errors.append('doctor.name: a supported full name is required')
    if parsed['synthetic'] != snapshot.synthetic:
        errors.append('synthetic: must match the immutable source snapshot')
    photo = parsed['doctor']['profile_photo_url']
    if photo:
        try:
            url = urlsplit(photo)
            # This is a reference only; no image is downloaded or rendered here.
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or any(c.isspace() or ord(c) < 32 for c in photo)):
                errors.append('doctor.profile_photo_url: an absolute HTTPS image reference without credentials is required')
        except ValueError:
            errors.append('doctor.profile_photo_url: invalid URL')
    facts = fact_values(parsed)
    evidence = parsed['evidence']
    classifications = {}
    for i, contact in enumerate(parsed['contacts']):
        for key in ('type','scope'):
            classifications[f'contacts.{i}.{key}'] = (contact[key],contact['value'])
    for i, registration in enumerate(parsed['registration']):
        classifications[f'registration.{i}.status'] = (registration['status'],registration['number'])
    if len({e['id'] for e in evidence}) != len(evidence):
        errors.append('evidence: IDs must be unique')
    valid = {}
    for item in evidence:
        field = item['field']
        issue = None
        if item['source_url'] != snapshot.url:
            issue = 'source URL differs from the fetched snapshot'
        elif item['retrieved_at'] != snapshot.retrieved_at:
            issue = 'retrieval timestamp differs from the fetched snapshot'
        elif item['support_text'] not in snapshot.text:
            issue = 'support text does not occur verbatim in the fetched snapshot'
        elif field not in facts and field not in classifications:
            issue = 'evidence field does not name a nonempty proposed fact or permitted classification'
        elif field in classifications and item['value'] != classifications[field][0]:
            issue = 'classification evidence does not match the proposed classification'
        elif field in classifications and classifications[field][1].casefold() not in item['support_text'].casefold():
            issue = 'classification quote does not support this contact or registration value'
        elif field in facts and item['value'] != facts[field]:
            issue = 'evidence value does not match proposed value'
        elif field in facts and item['value'].casefold() not in item['support_text'].casefold():
            issue = 'value is absent from its supporting quote'
        if issue:
            errors.append(f'evidence {item["id"]}: {issue}')
        else:
            valid.setdefault(field, []).append(item)
    for field in facts:
        if field not in valid:
            errors.append(f'{field}: no valid source evidence')
    for i, c in enumerate(parsed['contacts']):
        if c['type'] == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', c['value']):
            errors.append(f'contacts.{i}.value: invalid email syntax')
        if c['type'] == 'phone' and not 7 <= len(re.sub(r'\D', '', c['value'])) <= 15:
            errors.append(f'contacts.{i}.value: invalid phone digit count')
        scope_evidence = valid.get(f'contacts.{i}.scope', [])
        if c['scope'] != 'unclassified':
            if not scope_evidence or not any(x['value'] == c['scope'] for x in scope_evidence):
                errors.append(f'contacts.{i}.scope: explicit source evidence is required')
            else:
                kind = c['type']
                labels = (f'central {kind}', f'hospital central {kind}', f'institution central {kind}') if c['scope'] == 'institution_central' else (f'doctor {kind}', f'doctor professional {kind}', f'direct {kind}', f'professional {kind}')
                # Require one explicit label directly attached to THIS value. Full matching
                # rejects unrelated/negated text such as "No doctor email is published".
                pattern = r'(?:' + '|'.join(re.escape(x) for x in labels) + r')\s*:?\s*' + re.escape(c['value'])
                if not any(re.fullmatch(pattern, x['support_text'].strip(), re.I) for x in scope_evidence if x['value'] == c['scope']):
                    errors.append(f'contacts.{i}.scope: source needs an explicit label directly attached to this contact value')
    for i, r in enumerate(parsed['registration']):
        if r['status'] != 'provider_claimed':
            errors.append(f'registration.{i}.status: independent registry verification is not implemented; only provider_claimed can be approved in this starter')
    return sorted(set(errors))
