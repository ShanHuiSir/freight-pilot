import json,os,unittest,urllib.error
from unittest.mock import patch
from freight.agent import extract,completion,ModelError
from freight.core import ValidationError
class AgentTests(unittest.TestCase):
 def test_offline_parse(self):
  with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):
   r=extract('800 报价单位，从 PORT06 到 PORT09，3 天内到达，DTD AIR')
   self.assertEqual(r['order']['weight'],'800');self.assertTrue(r['requires_confirmation']);self.assertEqual(r['meta']['source'],'本地固定格式解析')
 def test_offline_missing_weight(self):
  with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}),self.assertRaises(ValidationError):extract('从 PORT06 到 PORT09')
 def test_model_missing_fields(self):
  with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-placeholder'}),patch('freight.agent.completion',return_value=(json.dumps({'weight':800,'origin':'PORT06','destination':None}),{})),self.assertRaises(ValidationError):extract('送货')
 def test_model_bad_json(self):
  with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-placeholder'}),patch('freight.agent.completion',return_value=('broken JSON',{})),self.assertRaises(ModelError):extract('送货')
 def test_network_error_explained(self):
  with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-placeholder'}),patch('urllib.request.urlopen',side_effect=urllib.error.URLError('offline')),self.assertRaises(ModelError) as c:completion([])
  self.assertIn('规则计算仍可使用',str(c.exception))
