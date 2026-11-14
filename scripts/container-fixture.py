"""Explicit test-image-only server; never shipped in the runtime image."""
from pathlib import Path
import os
import uvicorn

from harness.api.server import create_app
from harness.application.runtime import Runtime
from harness.core.config import load_config


async def synthetic_runner(config, prompt, context, caps):
    caps.on_usage(3, 3)
    return "Synthetic container acceptance result"


def runtime_factory(config, store):
    store.set_setting("nightlyDreamEnabled", False)
    return Runtime(config, store=store, runner=synthetic_runner)


config = load_config()
if config.api_key != "synthetic-container-no-network":
    raise RuntimeError("Container fixture requires the explicit synthetic test configuration")
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
uvicorn.run(create_app(config, runtime_factory), host=config.listen_host, port=config.port,
            workers=1, proxy_headers=False, access_log=False)
