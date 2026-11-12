"""Provision the capture-only revision of the existing JP replies workflow on the Dell."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from copy import deepcopy

import reply_capture
from reply_capture_fixture import build

BASE=Path(__file__).resolve().parent
APP=Path("/opt/jp-reply-capture")
STATE=Path("/var/lib/jp-reply-capture")
CONF=Path("/etc/jp-reply-capture")
UNIT=Path("/etc/systemd/system")
OVERRIDE=Path("/run/systemd/system/jp-reply-capture.service.d")
SOURCE=Path("/var/lib/jp-migracao-revisao/entrada-operacional")
WID="Bfk4YqHeYvQWpqiD"
EXPECTED="476c17f0-8452-4316-a948-f4832655b381"
URL="/rest/workflows/"+WID
sys.path.insert(0,"/opt/atendimento-piloto/source")
from configure_flows import n8n_login

def save(path, value):
    with path.open("x") as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno())

def ctl(*args):
    subprocess.run(["systemctl",*args],check=True,stdout=subprocess.DEVNULL)

def shape(w):
    return {k:w[k] for k in ("name","nodes","settings","connections")}

def setup():
    os.umask(0o077)
    api=n8n_login()
    get=lambda:api.req("GET",URL)["data"]
    old=get()
    assert not old["active"] and not old.get("activeVersionId") and old["versionId"]==EXPECTED
    assert not any(p.exists() for p in (APP,STATE,CONF,OVERRIDE,UNIT/"jp-reply-capture.socket",UNIT/"jp-reply-capture.service"))
    assert not subprocess.check_output(["ss","-H","-ltn","sport = :18942"]).strip()
    for p in (APP,STATE,CONF): p.mkdir(mode=0o700)
    save(CONF/"workflow-original.json",old)
    for filename in ("reply_capture.py","reply_capture_http.py"):
        shutil.copy2(BASE/filename,APP/filename)
    for filename in ("jp-reply-capture.socket","jp-reply-capture.service"):
        shutil.copyfile(BASE/filename,UNIT/filename)
        (UNIT/filename).chmod(0o644)
    token=secrets.token_hex(32)
    (CONF/"token").write_text(token)
    q=sqlite3.connect((SOURCE/"data/index/receive.sqlite3").as_uri()+"?mode=ro",uri=True)
    with reply_capture.connect(STATE/"replies.sqlite3") as db: water=reply_capture.init(q,db)
    fixture=STATE/"fixture"
    build(fixture)
    OVERRIDE.mkdir(parents=True,mode=0o700)
    (OVERRIDE/"fixture.conf").write_text(f"[Service]\nEnvironment=JP_CAPTURE_SOURCE={fixture}/source\nEnvironment=JP_CAPTURE_DB={fixture}/fixture.sqlite3\n")
    ctl("daemon-reload")
    ctl("enable","--now","jp-reply-capture.socket")
    assert get()["versionId"]==EXPECTED
    cred=api.req("POST","/rest/credentials",{"name":"JP Dell · captura de respostas","type":"httpHeaderAuth","data":{"name":"Authorization","value":"Bearer "+token}})["data"]
    save(CONF/"credential-ref.json",{"id":cred["id"],"name":cred["name"]})
    timer,=[deepcopy(n) for n in old["nodes"] if n["type"].endswith("scheduleTrigger")]
    timer["name"]="Verificar respostas novas"
    node=dict(id=str(uuid.uuid4()),name="Guardar texto, emoji e reação por ID",type="n8n-nodes-base.httpRequest",typeVersion=4.2,position=[300,0],
      parameters=dict(method="POST",url="http://172.18.0.1:18942/tick",authentication="genericCredentialType",genericAuthType="httpHeaderAuth",sendHeaders=True,
        headerParameters={"parameters":[{"name":"X-JP-Workflow","value":"={{ $workflow.id }}"},{"name":"X-JP-Execution","value":"={{ $execution.id }}"}]},options={"timeout":20000}),
      credentials={"httpHeaderAuth":{"id":cred["id"],"name":cred["name"]}})
    new={**shape(old),"nodes":[timer,node],"connections":{timer["name"]:{"main":[[{"node":node["name"],"type":"main","index":0}]]}}}
    assert old["settings"]["saveDataErrorExecution"]==old["settings"]["saveDataSuccessExecution"]=="none"
    save(CONF/"capture-definition.json",new)
    assert get()["versionId"]==EXPECTED
    api.req("PATCH",URL,{**new,"versionId":EXPECTED})
    cur=get()
    try:
        assert shape(cur)==new and not cur["active"]
        api.req("POST",URL+"/activate",{"versionId":cur["versionId"]})
        active=get();assert active["active"] and active["activeVersionId"]==cur["versionId"]
    except Exception:
        rollback(api)
        raise
    save(CONF/"installed.json",dict(version=cur["versionId"],watermark=water,at=time.time()))
    print(json.dumps(dict(active=True,version=cur["versionId"],watermark=water,fixture=True)))

def rollback(api):
    cur=api.req("GET",URL)["data"]
    desired=json.loads((CONF/"capture-definition.json").read_text())
    assert cur["nodes"]==desired["nodes"] and cur["connections"]==desired["connections"]
    if cur["active"]: api.req("POST",URL+"/deactivate",{})
    curr=api.req("GET",URL)["data"]
    old=json.loads((CONF/"workflow-original.json").read_text())
    api.req("PATCH",URL,{**shape(old),"versionId":curr["versionId"]})
    check=api.req("GET",URL)["data"]
    assert not check["active"] and shape(check)==shape(old)
    ctl("disable","--now","jp-reply-capture.socket")
    ctl("stop","jp-reply-capture.service")

def promote():
    f=STATE/"fixture/fixture.sqlite3"
    with sqlite3.connect(f.as_uri()+"?mode=ro",uri=True) as db:
        summary=[json.loads(row[0]) for row in db.execute("SELECT summary FROM ticks")]
        assert any(r["kinds"]=={"text":1,"emoji_text":1,"reaction":2,"sticker":1,"duplicate":1} for r in summary)
        assert db.execute("SELECT count(*) FROM captured").fetchone()==(5,)
        assert db.execute("SELECT count(*) FROM captured WHERE disposition='removed' AND quoted_id='fiction-notice'").fetchone()==(1,)
    (OVERRIDE/"fixture.conf").unlink()
    OVERRIDE.rmdir()
    ctl("daemon-reload")
    ctl("stop","jp-reply-capture.service")
    print(json.dumps(dict(fixture=True,live_source=True)))

if __name__=="__main__":
    os.umask(0o077)
    if sys.argv[1:]==["setup"]:setup()
    elif sys.argv[1:]==["promote"]:promote()
    elif sys.argv[1:]==["rollback"]:rollback(n8n_login())
    else:sys.exit(2)
