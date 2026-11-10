"""Project frozen, audited Dell drafts onto the operational Central entrypoint."""
import copy
from urllib.parse import urlsplit

BASE='http://jp-core-operacional:8791'
PRE='/api/n8n/stages/before-dispatch'
def stages(*names):return {'/api/n8n/stages/'+n for n in names}
PATHS={
    'clock':stages('plan-initial','reconcile-responses','plan-reminders','before-dispatch','send-initial','send-reminders','reactivation-review','reactivation-plan','reactivation-due','reactivation'),
    'reply':stages('reply-next','reply-plan','reply-execute','reply-review','support','before-dispatch')|{'/ops/classify'},
    'batch':{PRE,'/api/confirmation-batch/run'},'manual_sync':{'/api/agenda-sync/run'},'daily':stages('full-sync'),
    'renewal':stages('maintenance','renewal-run'),'error':stages('record-error'),
    'registration':stages('contact-outbox')|{'/api/contact-registration/'+x for x in ['claim','update','result']},
    'manual':{PRE,'/api/n8n/manual'},'brand':stages('brand-due','before-dispatch','brand-send')}
PRIVACY={'timezone':'America/Cuiaba','saveDataSuccessExecution':'none','saveDataErrorExecution':'none','saveExecutionProgress':False,'saveManualExecutions':False}

def compile_graph(source,role,workflow,role_credential,hook_credential,error_workflow):
    if source['active'] or source.get('staticData') or source.get('pinData') or role not in PATHS:raise ValueError('audited_inert_source_required')
    nodes=copy.deepcopy(source['nodes']);links=copy.deepcopy(source['connections'])
    if not all(n.get('disabled') is True and not n.get('credentials') for n in nodes):raise ValueError('non_inert_node')
    if role=='reply':
        chain=next(n for n in nodes if n['type'].endswith('.chainLlm'))
        if chain.get('onError')!='continueErrorOutput' or len(links[chain['name']]['main'])!=2:raise ValueError('human_error_branch_missing')
        removed=[n for n in nodes if n['type'].endswith(('.lmChatGoogleGemini','.outputParserStructured'))]
        if len(removed)!=2:raise ValueError('model_source_changed')
        for n in removed:
            if not all(k in ('ai_languageModel','ai_outputParser') for k in links[n['name']]):raise ValueError('unexpected_model_connections')
            del links[n['name']];nodes.remove(n)
        for key in ('retryOnFail','maxTries','waitBetweenTries'):
            chain.pop(key,None)
        chain.update(type='n8n-nodes-base.httpRequest',typeVersion=4.2,parameters={'method':'POST','url':BASE+'/ops/classify','sendBody':True,'specifyBody':'json','jsonBody':'={{ {task: $json.task.task_id, message: $json.input.message} }}'})
    paths={};hooks=[]
    for n in nodes:
        n['disabled']=False;p=n['parameters'];kind=n['type'].split('.')[-1]
        if kind=='webhook':
            if not hook_credential:raise ValueError('dedicated_hook_credential_required')
            p['authentication']='headerAuth';p['path']='ops-'+role+'-'+workflow.lower();n['credentials']={'httpHeaderAuth':hook_credential};hooks.append(p['path'])
        elif kind=='httpRequest':
            u=urlsplit(p['url'])
            if u.netloc not in ('jp-core-dell:8791','jp-attendance-api:8791') and not(role=='reply' and u.path=='/ops/classify' and u.netloc=='jp-core-operacional:8791'):raise ValueError('unexpected_source_host')
            if u.scheme!='http' or u.query or u.fragment or u.path not in PATHS[role] or p.get('method')!='POST' or n.get('retryOnFail'):raise ValueError('invalid_route')
            p.update(url=BASE+u.path,authentication='genericCredentialType',genericAuthType='httpHeaderAuth',sendHeaders=True,headerParameters={'parameters':[
                {'name':'X-JP-n8n-workflow-id','value':'={{ $workflow.id }}'},{'name':'X-JP-n8n-execution-id','value':'={{ $execution.id }}'},{'name':'X-JP-Node-ID','value':n['id']}]})
            n['credentials']={'httpHeaderAuth':role_credential};paths[n['id']]=u.path
        elif kind not in ('scheduleTrigger','errorTrigger','if','respondToWebhook','splitOut','set'):raise ValueError('unknown_node_type')
    if set(paths.values())!=PATHS[role] or len(paths)!=len(PATHS[role])+(role=='clock'):raise ValueError('stage_set_changed')
    settings={**PRIVACY,'executionOrder':'v1'}
    if role!='error':settings['errorWorkflow']=error_workflow
    graph={'name':source['name'],'nodes':nodes,'connections':links,'settings':settings}
    # Track the exact originating node for the repeated pre-sync stage.
    forced=[n['id'] for n in nodes if paths.get(n['id'])==PRE and n['parameters'].get('jsonBody')=={'force':True}]
    return graph,{'workflow':workflow,'role':role,'nodes':paths,'hooks':hooks,'forced':forced}
