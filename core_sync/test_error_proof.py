import unittest
from error_proof import bind,fixture,PRIVACY,ERROR,NEXT,SUPPORT

class ErrorConsumer(unittest.TestCase):
    def test_synthetic_source_reports_its_execution_and_then_terminates(self):
        f=fixture('synthetic',{'id':'test','name':'test'},'errors')
        self.assertEqual(f['settings']['errorWorkflow'],'errors')
        self.assertEqual(f['settings']['saveDataErrorExecution'],'none')
        self.assertEqual([n['type'].split('.')[-1] for n in f['nodes']],['webhook','respondToWebhook','stopAndError'])
        self.assertIn('$execution.id',f['nodes'][1]['parameters']['responseBody'])
        self.assertEqual(f['nodes'][0]['parameters']['authentication'],'headerAuth')
    def test_graph_does_not_enable_effects(self):
        def row(name,t,params=None):return {'name':name,'type':'n8n-nodes-base.'+t,'disabled':True,'parameters':params or {}}
        def http(name,path):return row(name,'httpRequest',{'url':'http://jp-core-dell:8791'+path})
        def edge(name):return [{'node':name,'type':'main','index':0}]
        g={'name':'fixture','versionId':'v','active':False,'settings':PRIVACY,'nodes':[row('clock','scheduleTrigger'),http('next',NEXT),row('has','if',{'value':'has_task'}),http('support',SUPPORT),http('execute','/api/n8n/stages/reply-execute')],'connections':{'clock':{'main':[edge('next')]},'next':{'main':[edge('has')]},'has':{'main':[edge('execute'),edge('support')]}}}
        out=bind(g,'reply',{'id':'test','name':'test'});self.assertTrue(out['nodes'][-1]['disabled'])
        self.assertTrue(all(x['disabled'] for x in g['nodes']))
        g['connections']['has']['main'].reverse()
        with self.assertRaises(ValueError):bind(g,'reply',{})
    def test_error_payload_expression_is_preserved(self):
        original='={{ $json.execution.id }}'
        g={'name':'fixture','versionId':'1','active':False,'settings':PRIVACY,'nodes':[{'name':'failed','type':'n8n-nodes-base.errorTrigger','disabled':True,'parameters':{}},{'name':'record','type':'n8n-nodes-base.httpRequest','disabled':True,'parameters':{'url':'http://jp-core-dell:8791'+ERROR,'jsonBody':original}}],'connections':{'failed':{'main':[[{'node':'record'}]]}}}
        self.assertEqual(bind(g,'error',{})['nodes'][1]['parameters']['jsonBody'],original)
if __name__=='__main__':unittest.main()
