from dataclasses import dataclass, field
from functools import lru_cache
import os
import math
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    database_url: str = 'sqlite:///./data/research.db'
    admin_token: str = field(default='', repr=False)
    mode: str = 'fixture'
    demo_auto_create: bool = True
    allowed_domains: tuple[str, ...] = ()
    worker_count: int = 3
    max_jobs: int = 100
    max_requests: int = 100
    requests_per_minute: int = 6
    request_timeout_seconds: int = 15
    max_response_bytes: int = 2_000_000
    max_redirects: int = 3
    lease_seconds: int = 120
    max_attempts: int = 3
    user_agent: str = 'OncologyResearchDirectory/2.0'
    user_agent_contact: str = ''
    llm_enabled: bool = False
    llm_provider: str = 'openai'
    openai_api_key: str = field(default='', repr=False)
    openai_model: str = ''
    mistral_api_key: str = field(default='', repr=False)
    mistral_model: str = ''
    llm_input_price_per_million: float | None = None
    llm_output_price_per_million: float | None = None
    max_llm_calls: int = 20
    llm_max_output_tokens: int = 4000
    llm_max_input_chars: int = 40000

    @property
    def identified_user_agent(self) -> str:
        return self.user_agent + (f' (+{self.user_agent_contact})' if self.user_agent_contact else '')

    def validate(self):
        if self.mode not in {'fixture', 'live'}:
            raise ValueError('MODE must be fixture or live')
        if not 2 <= self.worker_count <= 4:
            raise ValueError('WORKER_COUNT must be between 2 and 4')
        for key in ('max_jobs', 'max_requests', 'requests_per_minute', 'request_timeout_seconds',
                    'max_response_bytes', 'lease_seconds', 'max_attempts', 'max_llm_calls', 'llm_max_output_tokens', 'llm_max_input_chars'):
            if getattr(self, key) < 1:
                raise ValueError(f'{key} must be positive')
        if self.lease_seconds < 30:
            raise ValueError('LEASE_SECONDS must be at least 30')
        if not 0 <= self.max_redirects <= 5:
            raise ValueError('MAX_REDIRECTS must be 0..5')
        if self.llm_provider not in {'openai', 'mistral'}:
            raise ValueError('LLM_PROVIDER must be openai or mistral')
        if type(self.llm_enabled) is not bool:
            raise ValueError('LLM_ENABLED must be a boolean')
        if self.llm_enabled and (not getattr(self, self.llm_provider + '_api_key')
                                 or not getattr(self, self.llm_provider + '_model')):
            provider = self.llm_provider.upper()
            raise ValueError(f'LLM_ENABLED=1 requires {provider}_API_KEY and {provider}_MODEL')
        for key in ('llm_input_price_per_million', 'llm_output_price_per_million'):
            price = getattr(self, key)
            if price is not None and (type(price) not in (int, float) or not math.isfinite(price) or price < 0):
                raise ValueError(f'{key} must be a finite nonnegative USD amount or unset')
        if not self.user_agent or any(ord(c) < 32 or ord(c) > 126 for c in self.user_agent):
            raise ValueError('USER_AGENT must be a nonempty ASCII identification string')
        if self.user_agent_contact:
            contact = urlsplit(self.user_agent_contact)
            if (contact.scheme not in {'https', 'mailto'} or contact.username or contact.password
                    or (contact.scheme == 'https' and not contact.hostname)
                    or (contact.scheme == 'mailto' and '@' not in contact.path)
                    or any(ord(c) < 33 or ord(c) > 126 for c in self.user_agent_contact)):
                raise ValueError('USER_AGENT_CONTACT must be an HTTPS contact URL or mailto address')
        if self.llm_max_output_tokens < 16:
            raise ValueError('LLM_MAX_OUTPUT_TOKENS must be at least 16')
        if self.llm_max_output_tokens > 16000 or self.llm_max_input_chars > 100000:
            raise ValueError('LLM bounds exceed starter safety limits')
        return self

    def require_token(self):
        if len(self.admin_token) < 32 or self.admin_token.startswith('REPLACE_'):
            raise ValueError('Set ADMIN_TOKEN to a unique random token of at least 32 characters')

@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv('DATABASE_URL', Settings.database_url),
        admin_token=os.getenv('ADMIN_TOKEN', ''),
        mode=os.getenv('MODE', 'fixture'),
        demo_auto_create=os.getenv('DEMO_AUTO_CREATE', '1') == '1',
        allowed_domains=tuple(x.strip().lower().rstrip('.') for x in os.getenv('ALLOWED_DOMAINS', '').split(',') if x.strip()),
        worker_count=int(os.getenv('WORKER_COUNT', '3')),
        max_jobs=int(os.getenv('MAX_JOBS', '100')),
        max_requests=int(os.getenv('MAX_REQUESTS', '100')),
        requests_per_minute=int(os.getenv('REQUESTS_PER_MINUTE', '6')),
        request_timeout_seconds=int(os.getenv('REQUEST_TIMEOUT_SECONDS', '15')),
        max_response_bytes=int(os.getenv('MAX_RESPONSE_BYTES', '2000000')),
        max_redirects=int(os.getenv('MAX_REDIRECTS', '3')),
        lease_seconds=int(os.getenv('LEASE_SECONDS', '120')),
        max_attempts=int(os.getenv('MAX_ATTEMPTS', '3')),
        user_agent=os.getenv('USER_AGENT', Settings.user_agent),
        user_agent_contact=os.getenv('USER_AGENT_CONTACT', ''),
        llm_enabled=os.getenv('LLM_ENABLED', '0') == '1',
        llm_provider=os.getenv('LLM_PROVIDER', 'openai').strip().lower(),
        openai_api_key=os.getenv('OPENAI_API_KEY', ''),
        openai_model=os.getenv('OPENAI_MODEL', ''),
        mistral_api_key=os.getenv('MISTRAL_API_KEY', ''),
        mistral_model=os.getenv('MISTRAL_MODEL', ''),
        llm_input_price_per_million=_optional_price('LLM_INPUT_PRICE_PER_MILLION'),
        llm_output_price_per_million=_optional_price('LLM_OUTPUT_PRICE_PER_MILLION'),
        max_llm_calls=int(os.getenv('MAX_LLM_CALLS', '20')),
        llm_max_output_tokens=int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '4000')),
        llm_max_input_chars=int(os.getenv('LLM_MAX_INPUT_CHARS', '40000')),
    ).validate()


def _optional_price(name: str) -> float | None:
    value = os.getenv(name, '').strip()
    return float(value) if value else None
