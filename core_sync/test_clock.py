from copy import deepcopy
import unittest
from clock_graph import bind,STAGES

def source():
    nodes=[{'id':'start','name':'start','type':'n8n-nodes-base.scheduleTrigger','disabled':True,'parameters':{}}]
    links={}
    for i,stage in enumerate(STAGES):
        n={'id':'id-'+str(i),'name':'n'+str(i),'type':'n8n-nodes-base.httpRequest','disabled':True,
           'parameters':{'url':'http://jp-core-dell:8791/api/n8n/stages/'+stage}}
        if i==9:n['parameters']['jsonBody']={'force':True}
        links[nodes[-1]['name']]={'main':[[{'node':n['name'],'index':0,'type':'main'}]]};nodes.append(n)
    return {'name':'fixture','active':False,'nodes':nodes,'connections':links,'versionId':'test','settings':{'saveDataSuccessExecution':'none','saveDataErrorExecution':'none','saveExecutionProgress':False,'saveManualExecutions':False}}

class ClockTests(unittest.TestCase):
    def test_two_distinct_presyncs(self):
        s=source();before=deepcopy(s);g,order=bind(s,{'id':'fixture','name':'fixture'})
        self.assertEqual(s,before);self.assertEqual(s['connections'],g['connections'])
        self.assertEqual([n['stage'] for n in order],list(STAGES))
        self.assertNotEqual(order[3]['id'],order[9]['id'])
        for o,n in zip(order,g['nodes'][1:]):
            self.assertEqual(n['parameters']['headerParameters']['parameters'][-1]['value'],o['id'])
    def test_rejects_bypasses(self):
        for edit in [lambda s:s.update(active=True),lambda s:s['nodes'][4].update(alwaysOutputData=True),
                     lambda s:s['nodes'][10]['parameters'].update(jsonBody={}),
                     lambda s:s['nodes'][4]['parameters'].update(url='https://example.com'),
                     lambda s:s['nodes'][10].update(id=s['nodes'][4]['id']),
                     lambda s:s['connections']['start']['main'][0].append({'node':'n1','index':0})]:
            s=source();edit(s)
            with self.assertRaises(ValueError):bind(s,{})
if __name__=='__main__':unittest.main()
