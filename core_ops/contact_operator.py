#!/usr/bin/python3
"""Authenticated, local operator entry point. Never uses the Chatwoot token for writes."""
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

ROOT=Path('/etc/jp-cadastro-manual')
def need(value):
    if not value:raise ValueError('operation held')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def chat_get(route,token):
    request=urllib.request.Request('http://127.0.0.1:13300/api/v1/'+route,headers={'api_access_token':token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=6) as response:
        need(response.status==200 and response.geturl()==request.full_url)
        raw=response.read(300001);need(len(raw)<=300000);return json.loads(raw)

def identity(cfg,token,get=chat_get):
    need(isinstance(token,str) and re.fullmatch(r'[A-Za-z0-9_.-]{12,256}',token))
    user=get('profile',token)
    need(user['id']==cfg['uid'] and user['confirmed'] is True and
         hashlib.sha256(user['email'].strip().lower().encode()).hexdigest()==cfg['email_sha256'])
    accounts=[x for x in user.get('accounts',[]) if x.get('id')==cfg['account']]
    need(len(accounts)==1 and accounts[0]['role']=='agent' and accounts[0]['status']=='active')
    inboxes=get('accounts/'+str(cfg['account'])+'/inboxes',token)['payload']
    need(sum(row.get('id')==cfg['inbox'] for row in inboxes)==1)
    return 'chatwoot/'+str(cfg['account'])+'/'+str(cfg['uid'])

def inner(cfg,actor,body):
    code="""const fs=require('fs'),p=JSON.parse(fs.readFileSync(0));(async()=>{
      const r=await fetch('http://127.0.0.1:8792/api/contact-registration/manual',{method:'POST',redirect:'error',
        headers:{'content-type':'application/json','authorization':'Bearer '+p.key,'x-contact-actor':p.actor},
        body:JSON.stringify(p.body),signal:AbortSignal.timeout(5000)});
      const j=await r.json();console.log(JSON.stringify({http:r.status,result:r.ok?j.result:null}));})().catch(()=>process.exit(2));"""
    payload={'key':cfg['relay'],'actor':actor,'body':body}
    raw=subprocess.run(['docker','exec','-i',cfg['container'],'node','-e',code],input=json.dumps(payload).encode(),
                       capture_output=True,timeout=8,check=True)
    result=json.loads(raw.stdout);need(result['http']==200);return result['result']

def run(cfg,action,request,token):
    actor=identity(cfg,token)
    base={'request':request,'action':'inspect'};row=inner(cfg,actor,base)
    print(json.dumps(row,ensure_ascii=False,indent=2))
    if action=='inspect':return
    print('Confira o pedido, cliente, número e nome acima. Digite o mesmo ID para continuar.')
    need(input().strip()==request)
    data={k:row[k] for k in ('request','task','client','source','phone','version')}
    data.update(action=action,authorized=True)
    if action=='claim' and row['status']=='pending':
        print('No aparelho confira número, nome anterior (linha vazia para contato inexistente) e referência privada da observação. Informe em três linhas.')
        phone=input().strip();name=input().strip() or None;ref=input().strip()
        data['before']={'source':'device','phone':phone,'name':name,'reference':ref,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
    if action=='close':
        for source in ('web','device'):
            print('Observação atual em '+('WhatsApp Web' if source=='web' else 'aparelho')+'. Informe número E.164, nome visto e referência privada da prova, em três linhas.')
            phone=input().strip();name=input().strip();ref=input().strip()
            data[source]={'source':source,'phone':phone,'name':name,'reference':ref,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
    # User may have lost inbox access after opening the interactive prompt.
    need(identity(cfg,token)==actor)
    result=inner(cfg,actor,data)
    print(json.dumps({k:result[k] for k in ('request','task','mode','status','consumer_verified')},ensure_ascii=False))
    if action=='claim':
        print('Somente conferir o resultado anterior. Preserve o incerto se faltar prova.' if result['mode']=='observe_only'
              else 'Pedido em atendimento humano. Confira o nome anterior no aparelho e na Web antes de qualquer alteração individual autorizada.')

if __name__=='__main__':
    try:
        need(os.geteuid()==0 and len(sys.argv)==3 and sys.argv[1] in ('inspect','claim','close') and
             re.fullmatch(r'[A-Fa-f0-9-]{36}',sys.argv[2]))
        path=ROOT/'operator.json';stat=path.lstat();need(not path.is_symlink() and stat.st_uid==0 and stat.st_mode&0o077==0)
        config=json.loads(path.read_bytes());need(re.fullmatch(r'[0-9a-f]{64}',config['container']))
        run(config,sys.argv[1],sys.argv[2],getpass.getpass('Token pessoal da sua conta Chatwoot: '))
    except (Exception,KeyboardInterrupt):sys.exit('Operação retida. Releia o mesmo pedido antes de qualquer outra ação.')
