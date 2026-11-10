"""Restore the source-to-handler relationship on mapped n8n drafts."""
import hashlib
import json


def graph_hash(row):
    shape=[row[key] for key in ('nodes','connections','settings')]
    return hashlib.sha256(json.dumps(shape,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def prepare(original,handler,expected):
    if original['versionId']!=expected['version'] or graph_hash(original)!=expected['graph']:
        raise ValueError('workflow_changed')
    if original['id']==handler or original['settings'].get('errorWorkflow') not in (None,handler):
        raise ValueError('handler_conflict')
    return {'versionId':original['versionId'],'settings':{**original['settings'],'errorWorkflow':handler}}


def verify(before,after,handler):
    expected={**before,'settings':{**before['settings'],'errorWorkflow':handler}}
    if (before['id']!=after['id'] or before['active']!=after['active'] or
            graph_hash(after)!=graph_hash(expected)):
        raise ValueError('handler_update_unconfirmed')
