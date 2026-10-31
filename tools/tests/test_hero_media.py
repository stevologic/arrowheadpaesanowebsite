import hashlib
import json
import tempfile
import unittest
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tools.chiefs_narrative import hero_media, schema

NOW=datetime(2026,10,25,tzinfo=timezone.utc)
class HeroRights(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_3d_lifecycle(self):
        root=Path(__file__).resolve().parents[2]
        result=subprocess.run(['node','--test','tools/tests/playbook-3d.test.cjs'],cwd=root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_writer_cannot_grant_itself_image_rights(self):
        n=schema.normalize({'heroMediaId':'invented','heroMediaVerifiedAt':'2099','hero':{'license':'approved'},'xPhoto':{'mediaUrl':'https://example.test/fake.jpg'}},phase={},meta={})
        self.assertEqual(n['heroMediaId'],'article-illustration')
        self.assertEqual(n['heroMediaVerifiedAt'],'')
        self.assertEqual(n['xPhoto'],{})

    def test_permission_scope_expiry_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'docs/media-rights').mkdir(parents=True);(root/'public/images/hero').mkdir(parents=True)
            (root/'docs/media-rights/photo.md').write_text('Fixture: written commercial image permission')
            (root/'public/images/hero/photo.webp').write_bytes(b'fixture image bytes')
            row={'kind':'licensed-image','editionSlugs':['edition'],'sourceUrl':'https://x.com/Chiefs/status/123',
                 'image':'images/hero/photo.webp','alt':'Fixture subject','width':600,'height':400,
                 'sha256':hashlib.sha256(b'fixture image bytes').hexdigest(),
                 'rights':{'status':'approved','commercialUse':True,'providedImage':True,'rightsholder':'Fixture owner','license':'Written commercial permission',
                           'attribution':'Fixture credit','evidence':'docs/media-rights/photo.md','reviewedAt':'2026-10-01','expiresAt':'2026-12-31'}}
            self.assertTrue(hero_media.approved(row,'edition',root=root,now=NOW))
            self.assertFalse(hero_media.approved(row,'other',root=root,now=NOW))
            for key,value in [('license',''),('commercialUse',False),('expiresAt','2026-10-24'),('evidence','../private.md')]:
                copy=json.loads(json.dumps(row));copy['rights'][key]=value
                self.assertFalse(hero_media.approved(copy,'edition',root=root,now=NOW))
            row['kind']='x-post'
            self.assertFalse(hero_media.approved(row,'edition',root=root,now=NOW))
            row['kind']='licensed-image';(root/'public/images/hero/photo.webp').write_bytes(b'changed')
            self.assertFalse(hero_media.approved(row,'edition',root=root,now=NOW))

    def test_unavailable_catalog_falls_back_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            n={'slug':'edition','heroMediaId':'forged'}
            hero_media.attach(n,root=d,now=NOW)
            self.assertEqual(n['heroMediaId'],'article-illustration')

    def test_removed_timeline_has_no_loader_or_render_hook(self):
        root=Path(__file__).resolve().parents[2]
        html=(root/'layouts/partials/narrative-edition.html').read_text(encoding='utf-8')
        js=(root/'public/js/main.js').read_text(encoding='utf-8')
        self.assertNotIn('nrt-chiefs-feed',html)
        self.assertNotIn('initChiefsFeed',js)
        self.assertNotIn('createTimeline',js)
        self.assertIn('nrt-hero-media.html',html)

    def test_future_people_and_stats_have_no_model_image_urls(self):
        n=schema.normalize({'personnel':[{'move':'Signed','detail':'Test','players':['Player Name'],'image':'https://unlicensed'}],
                            'matchups':[{'unit':'Test','chiefsStat':'12','statSource':'https://unverified'}]},phase={},meta={})
        self.assertEqual(n['personnel'][0]['players'],['Player Name'])
        self.assertNotIn('image',n['personnel'][0])
        self.assertEqual(n['matchups'][0]['chiefsStat'],'')
