"""Isolated image test using the same control socket; never contains operator credentials."""
import base64, http.client,json,os,pathlib,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
os.umask(0o077)
image=sys.argv[1]
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);private=root/"private";legacy=root/"legacy";ipc=root/"ipc"
    for d in (private,legacy,ipc):d.mkdir()
    # Non-secret fixture files must be reachable inside the rootless container.
    for d in (private,ipc):os.chmod(d,0o777)
    os.chmod(legacy,0o755)
    pem=subprocess.check_output(["openssl","genpkey","-algorithm","RSA","-pkeyopt","rsa_keygen_bits:2048"],stderr=subprocess.DEVNULL)
    pub=subprocess.check_output(["openssl","pkey","-pubout"],input=pem)
    cfg=private/"config.json";cfg.write_text(json.dumps({"known_appointments":[{},{}],"generation":1,"public_key":pub.decode()}));cfg.chmod(0o444)
    scripts={
    "jp-appbarber-login-worker.mjs":"""
export async function loginWithBrowser({credentials,browserFactory,handoff,jobId}){
 if(credentials.userid!=="synthetic"||credentials.password!=="fixture-only")throw Error("credentials_rejected");
 const b=await browserFactory.launch();try{const c=await b.newContext({});const p=await c.newPage();
 await p.setContent("<h1>Fixture</h1>");const png=await p.screenshot();
 await handoff.report({reason_code:"captcha_or_otp_required"});await handoff.frame({png_base64:png.toString("base64")});
 for(let i=0;i<100;i++){const r=await handoff.poll();if(r.action?.key==="Enter")return {origin:"https://sistema.appbarber.com.br",cookieHeader:"synthetic=value"};await new Promise(r=>setTimeout(r,100));}
 throw Error("human_timeout");}finally{await b.close();}
}""","jp-appbarber-session-renewal.mjs":"""
export async function verifyCandidateSession({session,knownAppointments}){if(knownAppointments.length!==2)throw Error("assert");return {session,identity:{matched_count:2,verified_at:new Date().toISOString()}}}
"""}
    for n,s in scripts.items():
        f=legacy/n;f.write_text(s);f.chmod(0o444)
    args=["docker","run","-d","--rm","--network","none","--read-only","--cap-drop=ALL","--security-opt=no-new-privileges","--tmpfs","/tmp:rw,nosuid,nodev,size=160m","--memory","640m","--shm-size","96m","--pids-limit","160","-v",str(legacy)+":/legacy:ro","-v",str(private)+":/private:rw","-v",str(ipc)+":/ipc:rw",image]
    cid=subprocess.check_output(args,text=True).strip()
    class Conn(http.client.HTTPConnection):
        def connect(self):
            self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(3);self.sock.connect(str(ipc/"operator.sock"))
    def call(x):
        c=Conn("local");c.request("POST","/operate",json.dumps(x));r=c.getresponse();out=json.loads(r.read());c.close();return r.status,out
    try:
        for _ in range(100):
            if (ipc/"operator.sock").exists():break
            time.sleep(.1)
        assert call({"op":"status"})[1]["state"]=="idle"
        # UID 65532 owns this socket; CI helper may run as root for the fixture.
        assert call({"op":"start","userid":"synthetic","password":"fixture-only"})[0]==202
        assert call({"op":"start","userid":"synthetic","password":"fixture-only"})[0]==409
        for _ in range(100):
            status=call({"op":"status"})[1]
            if status.get("frame"):break
            if not status["attempting"]:raise AssertionError(status["state"])
            time.sleep(.1)
        assert base64.b64decode(status["frame"]).startswith(b"\x89PNG")
        assert call({"op":"action","action":"press","key":"Enter"})[0]==200
        for _ in range(100):
            if call({"op":"status"})[1]["state"]=="verified_candidate":break
            time.sleep(.1)
        sealed=(private/"verified-candidate.sealed.json").read_text();assert "synthetic=value" not in sealed
        envelope=json.loads(sealed);assert envelope["identity_matches"]==2 and envelope["generation"]==2
        assert call({"op":"start","userid":"synthetic","password":"fixture-only"})[0]==409
        print("isolated Chromium challenge, operator action and sealed candidate passed")
    finally:subprocess.run(["docker","stop","-t","3",cid],stdout=subprocess.DEVNULL,check=False)
