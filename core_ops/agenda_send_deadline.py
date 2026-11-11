"""Adapt only the pinned Dell legacy confirmation call to its local broker deadline."""
import hashlib

SOURCE = "72dfeef2389172d855781cc8ce05d924df3d7a3bdbb2eef070b2fceb6aa68833"


def transform(raw):
    if hashlib.sha256(raw).hexdigest() != SOURCE:
        raise ValueError("unexpected_legacy_revision")
    start = raw.index(b"export async function sendIndividualConfirmation(")
    end = raw.index(b"export async function ", start + 1)
    method = raw[start:end]
    # Local broker makes two bounded (20 s) calls: verify identity and send once.
    # A timeout still returns delivery_unknown; no retry is introduced.
    match = b"signal:AbortSignal.timeout(5000)"
    if method.count(match) != 1 or b"'/message/sendText/'" not in method:
        raise ValueError("unexpected_confirmation_method")
    return raw[:start] + method.replace(match,b"signal:AbortSignal.timeout(55000)") + raw[end:]
