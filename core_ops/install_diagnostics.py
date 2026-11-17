"""Prepare diagnostics against the exact running source; never touches state."""
import hashlib

SERVER='e99dd5ad3be3aa63922baa06e01b61cfc96d20ba16bcc5aae8770e8271f00a7b'
TRANSPORT='24f9855b236727c3031a212338a44e46f3c48c00ad085c0b0b9fa7541b576a06'

def prepare_server(source):
    if hashlib.sha256(source.encode()).hexdigest()!=SERVER:
        raise ValueError('server_source_changed')
    old='const child=spawn(process.execPath,["--import","/ops-app/transport.mjs","/legacy/scripts/jp-evolution-local-api.mjs"],{env,stdio:"ignore"});'
    if source.count(old)!=1:
        raise ValueError('child_anchor_changed')
    return 'import {observeChild,installProcessDiagnostics} from "./diagnostics.mjs";\ninstallProcessDiagnostics();\n'+source.replace(old,old.replace('stdio:"ignore"','stdio:["ignore","ignore","pipe"]')+'\nobserveChild(child);')

def prepare_transport(source):
    if hashlib.sha256(source.encode()).hexdigest()!=TRANSPORT:
        raise ValueError('transport_source_changed')
    return 'import {installProcessDiagnostics} from "./diagnostics.mjs";\ninstallProcessDiagnostics();\n'+source
