"""Reuse native expressions, branches and trigger types in three inert drafts."""
from copy import deepcopy
from urllib.parse import urlsplit
from clock_graph import ORIGIN

PRE='/api/n8n/stages/before-dispatch'
ROUTES={
 'batch':[PRE,'/api/confirmation-batch/run'],
 'contact-queue':['/api/n8n/stages/contact-outbox','/api/contact-registration/claim','/api/contact-registration/result'],
 'contact-hook':['/api/contact-registration/claim','/api/contact-registration/update','/api/contact-registration/result'],
 'brand-paused':['/api/n8n/stages/brand-due'],
 'brand-priority':['/api/n8n/stages/brand-due',PRE,'/api/n8n/stages/brand-send']}

def bind(row,scenario,credential):
    role=scenario.split('-')[0];routes=ROUTES[scenario]
    expected={v for k,values in ROUTES.items() if k.split('-')[0]==role for v in values}
    if row['active'] or row.get('activeVersionId') or row.get('staticData') or row.get('pinData'):
        raise ValueError('inert_draft_required')
    if any(row['settings'].get(k)!=v for k,v in {'saveDataErrorExecution':'none','saveDataSuccessExecution':'none','saveManualExecutions':False,'saveExecutionProgress':False}.items()):raise ValueError('retention_changed')
    nodes=deepcopy(row['nodes']);seen={};types=[]
    for n in nodes:
        if n.get('disabled') is not True or n.get('credentials') or n.get('alwaysOutputData') or n.get('retryOnFail'):raise ValueError('unsafe_source_node')
        n['disabled']=False;kind=n['type'].split('.')[-1];types.append(kind);p=n['parameters']
        if kind=='httpRequest':
            route=urlsplit(p['url'])
            if route.netloc!='jp-core-dell:8791' or route.scheme!='http' or route.query or route.fragment or route.path not in expected or route.path in seen:raise ValueError('unexpected_stage')
            seen[route.path]={'id':n['id'],'stage':route.path.rsplit('/',1)[-1],'path':route.path}
            p.update(url=ORIGIN+route.path,authentication='genericCredentialType',genericAuthType='httpHeaderAuth',sendHeaders=True,
                headerParameters={'parameters':[{'name':'X-JP-n8n-workflow-id','value':'={{ $workflow.id }}'},{'name':'X-JP-n8n-execution-id','value':'={{ $execution.id }}'},{'name':'X-JP-Node-ID','value':n['id']}]},options={'timeout':60000})
            n['credentials']={'httpHeaderAuth':credential}
        elif kind=='webhook':
            p['authentication']='headerAuth';n['credentials']={'httpHeaderAuth':credential}
    extras={'batch':['webhook'],'brand':['scheduleTrigger'],'contact':['webhook','scheduleTrigger','if','splitOut','set']}
    if set(seen)!=expected or sorted(t for t in types if t!='httpRequest')!=sorted(extras[role]):raise ValueError('unknown_nodes')
    return {'name':row['name'],'nodes':nodes,'connections':row['connections'],'settings':row['settings'],'versionId':row['versionId']},[seen[r] for r in routes]
