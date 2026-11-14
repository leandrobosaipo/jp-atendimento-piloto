"""Expose exactly two instance-scoped reads to the native Central preflight."""
import json,os,re,socket,struct,urllib.parse,urllib.request
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def probe(action,cfg,open_url):
    instance=cfg['instance']
    if cfg['origin']!='https://evolution.codigo5.com.br' or not re.fullmatch(r'[\w-]{1,96}',instance):raise ValueError('target')
    if action=='state':path='/instance/connectionState/'+instance
    elif action=='identity':path='/instance/fetchInstances?'+urllib.parse.urlencode({'instanceName':instance})
    else:raise ValueError('operation')
    req=urllib.request.Request(cfg['origin']+path,method='GET',headers={'apikey':cfg['token'],'CF-Access-Client-Id':cfg['client_id'],'CF-Access-Client-Secret':cfg['client_secret']})
    with open_url(req,timeout=8) as response:
        raw=response.read(262145)
        if response.status!=200 or len(raw)>262144:raise ValueError('response')
    data=json.loads(raw)
    if action=='state':
        v=data['instance']
        if v.get('instanceName')!=instance or v.get('state') not in ('open','close','connecting'):raise ValueError('state_identity')
        return {'instance':{'instanceName':instance,'state':v['state']}}
    if not isinstance(data,list) or len(data)!=1:raise ValueError('identity_count')
    row=data[0]
    if row.get('name')!=instance or row.get('integration')!='WHATSAPP-BAILEYS' or not re.fullmatch(r'[1-9]\d{7,14}@s\.whatsapp\.net',row.get('ownerJid','')):raise ValueError('unverified_identity')
    return [{k:row[k] for k in ('name','integration','ownerJid')}]

def serve():
    if os.environ.get('LISTEN_PID')!=str(os.getpid()) or os.environ.get('LISTEN_FDS')!='1':raise SystemExit('socket_required')
    cfg=json.loads((Path(os.environ['CREDENTIALS_DIRECTORY'])/'reader').read_bytes())
    opener=urllib.request.build_opener(NoRedirect());listener=socket.socket(fileno=3)
    while True:
        conn,_=listener.accept()
        with conn:
            try:
                if struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=64681:raise ValueError('peer')
                conn.settimeout(3)
                with conn.makefile('rb') as stream:raw=stream.readline(65)
                if not raw.endswith(b'\n') or len(raw)>64:raise ValueError('request')
                ask=json.loads(raw)
                if set(ask)!={'action'}:raise ValueError('schema')
                reply={'status':200,'body':probe(ask['action'],cfg,opener.open)}
            except Exception:reply={'status':503,'body':{'error':'instance_preflight_unavailable'}}
            try:conn.sendall(json.dumps(reply).encode()+b'\n')
            except OSError:pass
if __name__=='__main__':serve()
