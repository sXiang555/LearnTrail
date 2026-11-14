"""Offline contracts for the explicit research demo catalog; no API calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('research_demo', ROOT / 'scripts/seed_research_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class ResearchDemoTest(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(demo.CATALOG.read_text())

    def test_catalog_is_explicit_and_external_guides_cannot_claim_full_text_rewards(self):
        self.assertEqual(64, len(demo.validate(self.catalog)))
        for paper in self.catalog['papers']:
            edit = demo.article_edit(paper)
            self.assertEqual('link', edit['sourceType'])
            self.assertEqual('markdown', edit['contentFormat'])
            self.assertEqual(paper['sourceUrl'], edit['sourceUrl'])
            self.assertTrue(edit['sourceAuthor'])
            self.assertIn('不是论文全文', edit['body'])
            self.assertLessEqual(len(edit['body']), 5000)

    def test_duplicate_sources_or_unlabelled_creators_are_rejected(self):
        duplicate = copy.deepcopy(self.catalog)
        duplicate['papers'][1]['sourceUrl'] = duplicate['papers'][0]['sourceUrl']
        with self.assertRaises(AssertionError):
            demo.validate(duplicate)
        unlabelled = copy.deepcopy(self.catalog)
        unlabelled['creators'][0]['displayName'] = 'A real research team'
        with self.assertRaises(AssertionError):
            demo.validate(unlabelled)

    def test_private_import_state_is_atomically_saved_with_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            demo.save_private(path, {'phase': 'draft'})
            demo.save_private(path, {'phase': 'published'})
            self.assertEqual({'phase': 'published'}, json.loads(path.read_text()))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual([path], list(path.parent.iterdir()))


if __name__ == '__main__':
    unittest.main()
