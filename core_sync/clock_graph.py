"""Bind an intact, unpublished schedule to a temporary, fixture-only handler."""
from copy import deepcopy
from urllib.parse import urlsplit

STAGES = ('plan-initial', 'reconcile-responses', 'plan-reminders', 'before-dispatch',
          'send-initial', 'send-reminders', 'reactivation-review', 'reactivation-plan',
          'reactivation-due', 'before-dispatch', 'reactivation')
ORIGIN = 'http://jp-clock-rehearsal:8791'

def bind(row, credential):
    if row['active'] or row.get('activeVersionId') or row.get('pinData') or row.get('staticData'):
        raise ValueError('inactive_unpinned_draft_required')
    if any(row['settings'].get(k) != v for k,v in {
        'saveDataSuccessExecution':'none','saveDataErrorExecution':'none',
        'saveExecutionProgress':False,'saveManualExecutions':False}.items()):
        raise ValueError('retention_changed')
    nodes = deepcopy(row['nodes'])
    triggers = [n for n in nodes if n['type'].endswith('.scheduleTrigger')]
    if len(triggers) != 1 or len(nodes) != 1+len(STAGES) or any(n.get('credentials') or n.get('disabled') is not True or n.get('alwaysOutputData') for n in nodes):
        raise ValueError('unexpected_source')
    current=triggers[0];current['disabled']=False
    path=[];seen={current['id']}
    for i, stage in enumerate(STAGES):
        edges=row['connections'][current['name']]['main']
        if len(edges)!=1 or len(edges[0])!=1 or edges[0][0]['index']!=0:raise ValueError('not_linear')
        current=next(n for n in nodes if n['name']==edges[0][0]['node'])
        p=current['parameters'];u=urlsplit(p['url'])
        if (current['id'] in seen or not current['type'].endswith('.httpRequest') or u.netloc!='jp-core-dell:8791' or
            u.path!='/api/n8n/stages/'+stage or u.query or u.fragment or p.get('jsonBody',{})!=({'force':True} if i==9 else {})):
            raise ValueError('graph_changed')
        seen.add(current['id']);path.append({'id':current['id'],'stage':stage});current['disabled']=False
        p.update(url=ORIGIN+u.path,method='POST',authentication='genericCredentialType',genericAuthType='httpHeaderAuth',sendHeaders=True,
                 headerParameters={'parameters':[{'name':'X-JP-n8n-workflow-id','value':'={{ $workflow.id }}'},
                    {'name':'X-JP-n8n-execution-id','value':'={{ $execution.id }}'},{'name':'X-JP-Node-ID','value':current['id']}]},
                 options={'timeout':60000})
        current['credentials']={'httpHeaderAuth':credential}
    if row['connections'].get(current['name'], {}).get('main'):raise ValueError('terminal_edge')
    return {'name':row['name'],'nodes':nodes,'connections':row['connections'],'settings':row['settings'],'versionId':row['versionId']},path
