"""Bind only three existing inactive draft graphs. No schedule is published."""
import copy
from urllib.parse import urlsplit

ROUTES={'manual':['/api/agenda-sync/run'],'daily':['/api/n8n/stages/full-sync'],
        'renewal':['/api/n8n/stages/maintenance','/api/n8n/stages/renewal-run'],
        'relay':['/api/n8n/stages/before-dispatch','/api/n8n/manual']}
def bind(row,role,base,ingress,hook_auth,hook_path):
    if row['active'] or row.get('activeVersionId') or row.get('pinData') or row.get('staticData'):
        raise ValueError('not_clean_inactive_draft')
    nodes=copy.deepcopy(row['nodes']);seen=[];triggers=[]
    for node in nodes:
        if node.pop('disabled',None) is not True or node.get('credentials'):
            raise ValueError('unexpected_live_node')
        p=node['parameters'];typ=node['type'].split('.')[-1]
        if typ=='httpRequest':
            route=urlsplit(p['url'])
            if route.hostname!='jp-core-dell' or route.path not in ROUTES[role] or route.query or route.fragment:
                raise ValueError('unknown_stage')
            seen.append(route.path)
            p.update(url=base+route.path,method='POST',authentication='genericCredentialType',genericAuthType='httpHeaderAuth',
                sendHeaders=True,headerParameters={'parameters':[
                    {'name':'X-JP-n8n-workflow-id','value':'={{ $workflow.id }}'},
                    {'name':'X-JP-n8n-execution-id','value':'={{ $execution.id }}'}]},
                options={'timeout':540000})
            node['credentials']={'httpHeaderAuth':ingress}
            if role=='manual':
                p.update(sendBody=True,specifyBody='json',jsonBody='={{ {job_id: $json.body.job_id, capability: $json.body.capability} }}')
        elif typ=='webhook' and role in ('manual','relay'):
            triggers.append(typ);p.update(path=hook_path,httpMethod='POST',authentication='headerAuth',responseMode='responseNode' if role=='relay' else 'onReceived')
            node['credentials']={'httpHeaderAuth':hook_auth}
        elif role=='relay' and typ in ('if','respondToWebhook'):
            pass
        elif typ=='scheduleTrigger' and role in ('daily','renewal'):
            triggers.append(typ)
        else:raise ValueError('unexpected_node')
        node.pop('webhookId',None)
    if sorted(seen)!=sorted(ROUTES[role]) or len(triggers)!=1:raise ValueError('graph_mismatch')
    settings={k:row['settings'][k] for k in ('timezone','saveExecutionProgress','saveManualExecutions','saveDataErrorExecution','saveDataSuccessExecution') if k in row['settings']}
    if settings.get('saveDataSuccessExecution')!='none' or settings.get('saveDataErrorExecution')!='none' or settings.get('saveManualExecutions') is not False:raise ValueError('retention_mismatch')
    return {'name':row['name'],'nodes':nodes,'connections':row['connections'],'settings':settings,'versionId':row['versionId']}
