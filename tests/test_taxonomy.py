import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from match_taxonomy import match
class Taxonomy(unittest.TestCase):
    def setUp(self):
        self.tax = {'types': [dict(id='type_design', name='Design', aliases=['draft'], selectable_for_upload=True)]}
    def test_alias_and_name(self):
        for intent in ({'name':' DRAFT '}, {'name':'Ｄｅｓｉｇｎ'}, {'id':'type_design'}):
            self.assertEqual(match(self.tax,'type',intent)['selector'], {'id':'type_design'})
    def test_approximate(self):
        result = match(self.tax,'type',{'name':'Designs'})
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['candidates'][0]['id'], 'type_design')
        self.assertNotIn('selector', result)
    def test_not_selectable(self):
        self.tax['types'][0]['selectable_for_upload'] = False
        for intent in ({'name':'Design'}, {'id':'type_design'}):
            self.assertEqual(match(self.tax,'type',intent)['status'], 'unresolved')
    def test_over_200(self):
        self.tax['types'] = [dict(id=f't{i}',name=f'Design{i}') for i in range(201)]
        result = match(self.tax,'type',{'name':'Design'})
        self.assertEqual(result['candidates'], [])
        self.assertIn('候选过多', result['reason'])
