"""Run the dependency-free geometry regressions under the existing CI gate."""
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

class PlaybookGeometry(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_geometry_and_scrubbing(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(['node', '--test', 'tools/tests/playbook.test.cjs'], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

class EditorialPresentation(unittest.TestCase):
    def test_game_day_focus_uses_upcoming_game_not_previous_result(self):
        from tools.chiefs_narrative.prompts import preview_guidance
        game = {'date':'2026-10-26T00:20:00Z'}
        close = preview_guidance({'nextGame':game}, [], datetime(2026,10,25,21,tzinfo=timezone.utc))
        early = preview_guidance({'nextGame':game}, [], datetime(2026,10,20,21,tzinfo=timezone.utc))
        self.assertIn('120 words', close)
        self.assertIn('upcoming opponent', close)
        self.assertNotIn('GAME-DAY', early)

    def test_optional_titles_and_verified_portrait_shapes(self):
        from tools.chiefs_narrative.schema import normalize
        result = normalize({'sectionTitles':{'gamePlan':'The Seattle assignment'},
            'runOfShow':[{'talkTrack':'old public script'}],
            'injuries':[{'player':'A','image':'javascript:bad','imageSource':'https://example.com'}]},
            phase={'mode':'preview'}, meta={})
        self.assertEqual(result['sectionTitles']['gamePlan'],'The Seattle assignment')
        self.assertEqual(result['runOfShow'][0]['talkTrack'],'old public script')
        self.assertEqual(result['injuries'][0]['image'],'')

    def test_retired_script_is_not_rendered(self):
        root = Path(__file__).resolve().parents[2]
        text = (root/'layouts/partials/narrative-edition.html').read_text(encoding='utf-8')
        self.assertNotIn('with .runOfShow',text)
        self.assertNotIn('YouTube template',text)
        self.assertIn('id="press-conferences"',text)
