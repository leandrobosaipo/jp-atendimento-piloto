"""Produce an offline candidate from precisely the audited bundle."""
import hashlib
import pathlib
import sys

BASE = 'c9605eb89e91defef00b6d31a8a7d43ee1cceb88b306e74f28186d7fbeb09674'
SERVICE = 'async writeJpContactName(e){return require("./scoped-contact.cjs").write(this,e,G)}'
ROUTE = 'async(s,e)=>{if(!Tl(s,e))return;return require("./scoped-contact.cjs").route(s,e,V,Vu)}'


def replace_region(text, start, end, replacement):
    if text.count(start) != 1:
        raise ValueError('unexpected build structure')
    first = text.index(start)
    last = text.index(end, first + len(start))
    return text[:first] + replacement + text[last:]


def candidate(raw):
    if hashlib.sha256(raw).hexdigest() != BASE:
        raise ValueError('wrong base digest')
    text = replace_region(raw.decode(), 'async writeJpContactName(e){', 'async updateProfileStatus(', SERVICE)
    return replace_region(text, 'async(s,e)=>{if(!Tl(s,e))return;if(process.env.JP_CONTACT_ADAPTER_WRITE_ENABLED',
                          ')}};var xn', ROUTE).encode()


if __name__ == '__main__':
    base, dest = map(pathlib.Path, sys.argv[1:])
    output = candidate(base.read_bytes())
    with dest.open('xb') as f:
        f.write(output)
    print(hashlib.sha256(output).hexdigest())
