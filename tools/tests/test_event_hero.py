"""Render actual current/archive pages to enforce event-scoped, honest media."""
import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which('hugo') or str(ROOT/'node_modules/hugo-bin/vendor/hugo.exe')


@unittest.skipUnless(Path(HUGO).is_file(), 'Hugo unavailable')
class EventHeroRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.temp.name)/'site'
        result = subprocess.run([HUGO, '--minify', '--destination', str(cls.dist)],
                                cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_archive_uses_its_own_event_and_honest_fallback(self):
        for path in (ROOT/'data/narrative_editions').glob('*.json'):
            n = json.loads(path.read_text(encoding='utf-8'))
            page = (self.dist/'narrative'/n['slug']/'index.html').read_text(encoding='utf-8')
            hero = html.unescape(re.search(r'<figure\b[^>]*class=[\"\']?nrt-hero-media\b.*?</figure>',page,re.S).group())
            mode = n['phase']['mode']
            event = (n.get('nextGame') if mode == 'preview' else n.get('lastGameReview') if mode == 'review' else {}) or {}
            subject = event.get('opponent') or 'Kansas City Chiefs'
            with self.subTest(slug=n['slug']):
                self.assertIn(subject,hero)
                self.assertNotIn('host-studio-red',hero)
                if 'article-illustration' in hero:
                    self.assertIn(n['headline'],hero)
                    self.assertIn('Event photograph not yet cleared.',hero)
                else:
                    self.assertIn('Licensed image',hero)

    def test_raw_official_images_are_not_republished_without_ledger(self):
        page = (self.dist/'index.html').read_text(encoding='utf-8')
        self.assertNotRegex(page,r'<img[^>]+static\.clubs\.nfl\.com')
        self.assertIn('Player profile',page)
        self.assertIn('Watch at Chiefs.com',page)
        self.assertIn('youtube.com/watch?v=xCnSucR_NCw',page)
        self.assertIn('youtube.com/watch?v=0ShMtltDtGM',page)
        self.assertIn('illustrative silhouette, not a photograph',page)
