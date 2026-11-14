"""Local settings; credentials never appear in status output."""
from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass
class Config:
    root: Path
    data_dir: Path
    serve_frontend: bool = True
    frontend_dir: Path | None = None
    port: int = 8787
    token: str = ''
    api_key: str = ''
    model: str = 'gpt-6-luna'
    google_client_id: str = ''
    google_client_secret: str = ''
    encryption_secret: str = ''
    browser_executable: str | None = None
    browser_channel: str | None = None
    docker_enabled: bool = False
    docker_image: str = 'python:3.12-slim'
    daily_token_limit: int = 200000
    run_token_reservation: int = 100000
    mastodon_base_url: str = ''
    shopify_store: str = ''
    homeassistant_base_url: str = ''
    container_mode: bool = False
    allowed_hosts: tuple[str, ...] = ()
    mcp_trusted_loopback_urls: tuple[str, ...] = ()

    @property
    def listen_host(self) -> str:
        return '0.0.0.0' if self.container_mode else '127.0.0.1'


def container_hosts(value: str) -> tuple[str, ...]:
    """Explicit Host header authorities, never wildcard/proxy trust."""
    result = []
    for item in value.split(','):
        host = item.strip().lower()
        if not host: continue
        match = re.fullmatch(r'(localhost|127\.0\.0\.1|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?):([1-9][0-9]{0,4})', host)
        if not match or int(match[2]) > 65535 or '..' in match[1]:
            raise ValueError('HARNESS_ALLOWED_HOSTS must contain exact host:port values, without URLs or wildcards')
        if host not in result: result.append(host)
    if len(result) > 20: raise ValueError('At most 20 container hosts may be configured')
    return tuple(result)


def _default_root() -> Path:
    source = Path(__file__).resolve()
    # Locked non-editable installs live in the workspace's .venv in the image.
    # An editable src-layout install continues to resolve to the checkout root.
    for parent in source.parents:
        if parent.name == '.venv': return parent.parent
    return source.parents[4]


def load_config(root: Path | str | None = None) -> Config:
    root = Path(root or os.environ.get("HARNESS_ROOT") or _default_root()).resolve()
    values = {}
    path = root / '.dev.vars'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match = re.fullmatch(r'\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*', line)
            if match:
                value = match[2]
                values[match[1]] = value[1:-1] if len(value) >= 2 and value[0] in '\'"' and value[-1] == value[0] else value
    def get(key, fallback=''):
        return os.environ.get(key, values.get(key, fallback))
    if get('OPENAI_MODEL', 'gpt-6-luna') != 'gpt-6-luna':
        raise ValueError('Only gpt-6-luna is permitted. Correct OPENAI_MODEL.')
    port = int(get('LOCAL_PORT', '8787'))
    if not 1024 <= port <= 65535:
        raise ValueError('LOCAL_PORT must be between 1024 and 65535')
    container_mode = get('CONTAINER_MODE', 'false').lower()
    if container_mode not in ('true', 'false'):
        raise ValueError('CONTAINER_MODE must be true or false')
    allowed_hosts = container_hosts(get('HARNESS_ALLOWED_HOSTS'))
    if allowed_hosts and container_mode != 'true':
        raise ValueError('HARNESS_ALLOWED_HOSTS requires CONTAINER_MODE=true')
    return Config(serve_frontend=get('SERVE_FRONTEND', 'true').lower() == 'true',
                  frontend_dir=(root / get('FRONTEND_DIST_DIR', 'frontend/dist')).resolve(),
                  root=root, data_dir=(root / get('ASSISTANT_DATA_DIR', '.assistant')).resolve(), port=port,
                  token=get('HARNESS_TOKEN'), api_key=get('OPENAI_API_KEY'),
                  google_client_id=get('GOOGLE_CLIENT_ID'), google_client_secret=get('GOOGLE_CLIENT_SECRET'),
                  encryption_secret=get('CREDENTIAL_KEY', get('HARNESS_TOKEN')),
                  browser_executable=get('BROWSER_EXECUTABLE_PATH') or None, browser_channel=get('BROWSER_CHANNEL') or None,
                  docker_enabled=get('ENABLE_DOCKER_EXECUTION') == 'true', docker_image=get('DOCKER_IMAGE', 'python:3.12-slim'),
                  daily_token_limit=int(get('DAILY_TOKEN_LIMIT', '200000')), run_token_reservation=int(get('RUN_TOKEN_RESERVATION', '100000')),
                  mastodon_base_url=get('MASTODON_BASE_URL'),
                  shopify_store=get('SHOPIFY_STORE'), homeassistant_base_url=get('HOME_ASSISTANT_BASE_URL'),
                  container_mode=container_mode == 'true', allowed_hosts=allowed_hosts,
                  mcp_trusted_loopback_urls=tuple(value.strip() for value in get('MCP_TRUSTED_LOCAL_URLS').split(',') if value.strip()))
