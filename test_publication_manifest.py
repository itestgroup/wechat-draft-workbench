"""Check that the release inventory gate rejects tampering and unlisted files."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publication_gate', Path(__file__).parent / 'tools/check_manifest.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PublicationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'README.md').write_bytes(b'reviewed\n')
        self.manifest = {'files': {'README.md': hashlib.sha256(b'reviewed\n').hexdigest()}}
        self.save()

    def save(self):
        (self.root / 'UPSTREAM_MANIFEST.json').write_text(json.dumps(self.manifest))

    def run_gate(self, tracked=b'README.md\0UPSTREAM_MANIFEST.json\0'):
        with patch.object(gate, 'ROOT', self.root), patch.object(gate.subprocess, 'check_output', return_value=tracked):
            gate.main()

    def test_reviewed_tree_passes(self):
        self.run_gate()

    def test_modified_bytes_rejected(self):
        (self.root / 'README.md').write_bytes(b'changed\n')
        with self.assertRaises(SystemExit):
            self.run_gate()

    def test_unlisted_tracked_file_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_gate(b'README.md\0UPSTREAM_MANIFEST.json\0private.env\0')

    def test_path_outside_checkout_rejected(self):
        self.manifest = {'files': {'../outside': '0' * 64}}
        self.save()
        with self.assertRaises(SystemExit):
            self.run_gate(b'../outside\0UPSTREAM_MANIFEST.json\0')
