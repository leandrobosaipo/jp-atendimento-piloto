"""Exercise three synthetic records and restore all app databases into separate databases."""
import http.cookiejar
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from install import ROOT, SECRET, PORTS, dc, run, wait_http

MARKER = "PilotFixture0001"

def request(client, port, path, body=None, form=False, token=None):
    headers = {}
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    else:
        data=None
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(f"http://127.0.0.1:{port}{path}",data=data,headers=headers)
    try:
        with client.open(req,timeout=45) as res:
            return json.load(res)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{path} returned HTTP {error.code}") from None

def sql(db,query):
    return dc("exec","-T","postgres","psql","-v","ON_ERROR_STOP=1","-U","postgres","-d",db,"-Atc",query)

def main():
    os.umask(0o077)
    ready=json.loads((SECRET/"ready.json").read_text())
    if run("git","rev-parse","HEAD")!=ready["commit"] or run("git","status","--porcelain"):
        raise SystemExit("Expected the verified clean release")
    evidence=SECRET/"fixture"
    evidence.mkdir(mode=0o700)
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    # Run the Rails console with the web/worker paused to avoid overlapping Rails heaps.
    dc("stop","worker","chat")
    try:
        ruby='a=Account.create!(name: "PilotFixture0001"); a.contacts.create!(name: "Fictitious contact", identifier: "PilotFixture0001"); puts "created"'
        dc("run","--rm","--no-deps","chat","bundle","exec","rails","runner",ruby)
    finally:
        dc("up","-d","--no-deps","chat","worker")
    wait_http("chat")
    flowpw=next(line.split("=",1)[1] for line in (SECRET/"flow.env").read_text().splitlines() if line.startswith("LANGFLOW_SUPERUSER_PASSWORD="))
    token=request(client,17860,"/api/v1/login",{"username":"fixture","password":flowpw},form=True)["access_token"]
    flow=request(client,17860,"/api/v1/flows/",{"name":MARKER,"data":{"nodes":[],"edges":[]}},token=token)
    assert flow["name"] == MARKER
    import secrets
    password="Aa7!"+secrets.token_hex(20)
    with (SECRET/"n8n-owner").open("x") as f:
        f.write(password)
    request(client,15678,"/rest/owner/setup",{"email":"fixture@example.invalid","firstName":"Pilot","lastName":"Fixture","password":password})
    workflow={"name":MARKER,"nodes":[{"id":"9de58d3e-40da-4afd-9754-e5cb30621756","name":"Manual","type":"n8n-nodes-base.manualTrigger","typeVersion":1,"position":[0,0]}],"connections":{},"settings":{},"active":False}
    result=request(client,15678,"/rest/workflows",workflow)["data"]
    assert result["name"]==MARKER and result["active"] is False
    checks={"chat":("contacts","identifier"),"flow":("flow","name"),"clock":("workflow_entity","name")}
    dc("restart","chat","worker","clock","flow")
    for service in PORTS:
        wait_http(service)
    if int(dc("exec","-T","redis","redis-cli","SCARD","processes"))<1:
        raise RuntimeError("Sidekiq heartbeat missing")
    records={}
    for db,(table,field) in checks.items():
        query=f"SELECT count(*) FROM {table} WHERE {field}='{MARKER}'"
        if sql(db,query)!="1":
            raise RuntimeError("Live database fixture missing in "+db)
        target=db+"_fixture_restore"
        if sql("postgres",f"SELECT count(*) FROM pg_database WHERE datname='{target}'")!="0":
            raise RuntimeError("Restore database already exists")
        archive=evidence/(db+".dump")
        base=["docker","compose","exec","-T","postgres"]
        with archive.open("xb") as output:
            subprocess.run([*base,"pg_dump","-Fc","-U","postgres","-d",db],cwd=ROOT,stdout=output,check=True)
        dc("exec","-T","postgres","createdb","-U","postgres",target)
        sql("postgres",f"REVOKE ALL ON DATABASE {target} FROM PUBLIC")
        with archive.open("rb") as source:
            subprocess.run([*base,"pg_restore","--exit-on-error","--no-owner","-U","postgres","-d",target],cwd=ROOT,stdin=source,check=True)
        if sql(target,query)!="1":
            raise RuntimeError("Restored fixture missing in "+target)
        records[db]={"sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),"bytes":archive.stat().st_size,"live":1,"restored":1}
    (evidence/"result.json").write_text(json.dumps(records,indent=2))
    print("Three synthetic records survived restart and matched across three isolated database restores.")
if __name__=="__main__":
    main()
