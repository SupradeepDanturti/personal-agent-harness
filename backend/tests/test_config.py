"""Configuration stays anchored to the checkout after moving to a src layout."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core.config import load_config


class ConfigTests(unittest.TestCase):
    def test_default_root_is_independent_of_launch_directory(self):
        checkout = Path(__file__).resolve().parents[2]
        with patch.dict(os.environ, {}, clear=True):
            with patch('pathlib.Path.cwd', return_value=Path(tempfile.gettempdir())), patch('pathlib.Path.exists', return_value=False):
                config = load_config()
        self.assertEqual(config.root, checkout)
        # No alternate cwd-based state directory is silently created.
        self.assertEqual(config.data_dir.parent, checkout)

    def test_explicit_root_and_frontend_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / '.dev.vars').write_text(
                'SERVE_FRONTEND=false\nFRONTEND_DIST_DIR=custom-ui\n', encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)
            self.assertFalse(config.serve_frontend)
            self.assertEqual(config.frontend_dir, root / 'custom-ui')
            self.assertEqual(config.data_dir, root / '.assistant')

    def test_container_mode_changes_bind_only_when_explicit_and_uses_exact_hosts(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_config(folder).listen_host, '127.0.0.1')
            with patch.dict(os.environ, {'CONTAINER_MODE': 'true',
                    'HARNESS_ALLOWED_HOSTS': 'harness:8787, localhost:8787,harness:8787',
                    'MCP_TRUSTED_LOCAL_URLS': ' http://127.0.0.1:9000/mcp, ,http://localhost:9001/mcp '}, clear=True):
                config = load_config(folder)
                self.assertEqual(config.listen_host, '0.0.0.0')
                self.assertEqual(config.allowed_hosts, ('harness:8787', 'localhost:8787'))
                self.assertEqual(config.mcp_trusted_loopback_urls,
                    ('http://127.0.0.1:9000/mcp', 'http://localhost:9001/mcp'))

    def test_container_hosts_reject_wildcards_urls_and_local_mode_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in ('*', 'https://harness:8787', 'harness:99999', 'harness:8787/path', 'harness:8787\nother:8787'):
                with self.subTest(value=value), patch.dict(os.environ,
                        {'CONTAINER_MODE': 'true', 'HARNESS_ALLOWED_HOSTS': value}, clear=True):
                    with self.assertRaises(ValueError): load_config(folder)
            with patch.dict(os.environ, {'HARNESS_ALLOWED_HOSTS': 'harness:8787'}, clear=True):
                with self.assertRaisesRegex(ValueError, 'requires CONTAINER_MODE'):
                    load_config(folder)

    def test_noneditable_workspace_install_defaults_to_venv_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            installed = root / '.venv/lib/python3.14/site-packages/harness/core/config.py'
            with patch.dict(os.environ, {}, clear=True), patch('harness.core.config.__file__', str(installed)):
                self.assertEqual(load_config().root, root)

    def test_root_override_and_default_frontend_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch.dict(os.environ, {'HARNESS_ROOT': str(root)}, clear=True):
                config = load_config()
            self.assertEqual(config.root, root)
            self.assertEqual(config.frontend_dir, root / 'frontend' / 'dist')
