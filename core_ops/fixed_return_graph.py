from copy import deepcopy
from uuid import NAMESPACE_URL,uuid5

CLOCK='7Ur9XdO2FiFEnMhY'
BASE='http://jp-core-operacional:8791/api/n8n/stages/'

def build(clock,monitor,manifest):
    w=deepcopy({k:clock[k] for k in ('name','nodes','connections','settings')})
    m=deepcopy(manifest)
    spec=m['clock']
    if clock['id']!=CLOCK or spec['workflow']!=CLOCK:
        raise ValueError('wrong_clock')
    tail,=[n for n in w['nodes'] if n['parameters'].get('url')==BASE+'send-reminders']
    source,=[n for n in monitor['nodes'] if n['parameters'].get('url')=='http://172.18.0.1:18963/triagem']
    if tail['name'] in w['connections'] or len(w['nodes'])!=7:
        raise ValueError('clock_changed')
    names=['Selecionar convite da lista aprovada','Atualizar a agenda para este convite',
           'Conferir cliente e as duas vagas','Processar somente este convite']
    routes=['fixed-return-next','before-dispatch',None,'fixed-return']
    previous=tail['name'];ids={}
    for i,(name,route) in enumerate(zip(names,routes)):
        node=deepcopy(tail if route else source)
        node['name']=name;node['id']=str(uuid5(NAMESPACE_URL,'jp-fixed-returns-20261123/'+name))
        node['position']=[tail['position'][0]+250*(i+1),tail['position'][1]]
        p=node['parameters']
        if route:
            p['url']=BASE+route
            header,=[v for v in p['headerParameters']['parameters'] if v['name'].lower()=='x-jp-node-id']
            header['value']=node['id'];spec['nodes'][node['id']]=p['url']
            if route=='before-dispatch':
                p.update(sendBody=True,specifyBody='json',jsonBody='{"force":true}')
                spec['forced'].append(node['id'])
            else:ids[route]=node['id']
        else:
            p['url']+='/convite'
            p['headerParameters']['parameters'].append({'name':'X-JP-Approval-Key',
                'value':"={{ $('Selecionar convite da lista aprovada').first().json.approval_key }}"})
            p['options']={**p.get('options',{}),'timeout':60000}
        if node.get('retryOnFail') or node.get('alwaysOutputData'):
            raise ValueError('unexpected_retry_or_empty_output')
        w['nodes'].append(node)
        w['connections'][previous]={'main':[[{'node':name,'type':'main','index':0}]]}
        previous=name
    return w,m,ids
