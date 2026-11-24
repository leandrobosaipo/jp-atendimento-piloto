"""Bind the supervisor to the fielded capture handler without replacing adjacent features."""
ANCHOR = '                answer=PREFERENCE if exception else plan(question,config,slot_query,'
ADDITION = """                scope_file=conf/'conversation-supervisor.json'
                if not exception and question==text and scope_file.is_file():
                    from conversation_supervisor import review_turn
                    question=review_turn(text,seq,stamp,jid,aliases,db,ledger,read,json.loads(scope_file.read_bytes())) or text
"""


def bind(source):
    if source.count(ANCHOR)!=1 or ADDITION in source:
        raise ValueError('unexpected_capture_source')
    return source.replace(ANCHOR,ADDITION+ANCHOR)
