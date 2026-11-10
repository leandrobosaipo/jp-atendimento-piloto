"""Exercise the original Error Trigger and the no-task response branch."""
import copy
from uuid import uuid4
from urllib.parse import urlsplit

BASE='http://jp-core-transacional-dell:8791'
ERROR='/api/n8n/stages/record-error'
NEXT='/api/n8n/stages/reply-next'
SUPPORT='/api/n8n/stages/support'
PRIVACY={'saveDataErrorExecution':'none','saveDataSuccessExecution':'none','saveExecutionProgress':False,'saveManualExecutions':False}

def bind(row,role,credential):
    if row['active'] or row.get('activeVersionId') or row.get('pinData') or row.get('staticData'):
        raise ValueError('inactive_clean_draft_required')
    if any(row['settings'].get(k)!=v for k,v in PRIVACY.items()):
        raise ValueError('retention_changed')
    if role not in ('error','reply'):raise ValueError('unknown_role')
    nodes=copy.deepcopy(row['nodes'])
    if any(n.get('disabled') is not True or n.get('credentials') for n in nodes):
        raise ValueError('inert_source_required')
    enabled=[];allowed={ERROR} if role=='error' else {NEXT,SUPPORT};route_nodes={}
    for n in nodes:
        t=n['type'].split('.')[-1];p=n['parameters']
        if t=='httpRequest':
            u=urlsplit(p['url'])
            if u.path in allowed:
                if u.netloc!='jp-core-dell:8791' or u.query or u.fragment or u.path in route_nodes:
                    raise ValueError('unexpected_source_route')
                n['disabled']=False;p.update(url=BASE+u.path,method='POST',authentication='genericCredentialType',genericAuthType='httpHeaderAuth',
                    sendHeaders=True,headerParameters={'parameters':[{'name':'X-JP-n8n-workflow-id','value':'={{ $workflow.id }}'},
                    {'name':'X-JP-n8n-execution-id','value':'={{ $execution.id }}'}]},options={'timeout':15000})
                n['credentials']={'httpHeaderAuth':credential};route_nodes[u.path]=n['name']
        if (role=='reply' and t=='scheduleTrigger') or (role=='error' and t=='errorTrigger'):
            enabled.append(n['name']);n['disabled']=False
    if set(route_nodes)!=allowed or len(enabled)!=1:raise ValueError('source_graph_incomplete')
    links=row['connections']
    def to(name,index=0):return [v['node'] for v in links[name]['main'][index]]
    if role=='error':
        if len(nodes)!=2 or to(enabled[0])!=[route_nodes[ERROR]]:raise ValueError('error_graph_changed')
    else:
        branch=to(route_nodes[NEXT])
        if len(branch)!=1 or to(enabled[0])!=[route_nodes[NEXT]] or to(branch[0],1)!=[route_nodes[SUPPORT]]:
            raise ValueError('non_effect_branch_missing')
        condition=next(n for n in nodes if n['name']==branch[0] and n['type'].endswith('.if'))
        if 'has_task' not in str(condition['parameters']):raise ValueError('changed_reply_condition')
        condition['disabled']=False
    return {'name':row['name'],'nodes':nodes,'connections':links,'versionId':row['versionId'],'settings':{**PRIVACY,'timezone':'America/Cuiaba'}}

def fixture(path,credential,error_workflow):
    def node(name,kind,version,params,x):
        return {'id':str(uuid4()),'name':name,'type':'n8n-nodes-base.'+kind,'typeVersion':version,'parameters':params,'position':[x,0]}
    hook=node('Iniciar prova','webhook',2,{'path':path,'httpMethod':'POST','authentication':'headerAuth','responseMode':'responseNode'},0)
    hook['credentials']={'httpHeaderAuth':credential};hook['webhookId']=str(uuid4())
    receipt=node('Conferir ID de origem','respondToWebhook',1.4,{'respondWith':'json','responseBody':'={{ {execution: $execution.id, source: $workflow.id} }}','options':{'responseCode':202}},240)
    failure=node('Falha controlada','stopAndError',1,{'errorType':'errorMessage','errorMessage':'fixture_error_receipt'},480)
    links={first['name']:{'main':[[{'node':second['name'],'type':'main','index':0}]]} for first,second in ((hook,receipt),(receipt,failure))}
    return {'name':'Prova isolada do registro de falha','nodes':[hook,receipt,failure],'connections':links,'settings':{**PRIVACY,'executionOrder':'v1','errorWorkflow':error_workflow,'timezone':'America/Cuiaba'}}
