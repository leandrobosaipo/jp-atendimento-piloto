"""Create private human-review cases from already captured JP replies.

This module deliberately has no transport, agenda, or WhatsApp dependency.  It
only turns statuses already assigned by the core into idempotent case plans.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable


ALLOWED = {"identity_review", "visit_review", "human_review", "human_hold", "captured"}
STALE_CLAIM_SECONDS = 300


@dataclass(frozen=True)
class Case:
    capture: int
    marker: str
    origin_hash: str
    status: str
    kind: str
    disposition: str
    received_at: str
    message_id: str
    fingerprint: str
    note: str


def _origin(address_hashes: str) -> str:
    values = json.loads(address_hashes)
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and len(v) == 64 for v in values):
        raise ValueError("invalid_origin_hashes")
    return sorted(values)[0]


def suggested_action(status: str, kind: str, disposition: str) -> str:
    if status == "identity_review":
        return "Conferir a identidade antes de vincular o caso, responder ou alterar a agenda."
    if status == "visit_review" or disposition == "changed_review":
        return "Retorno sugerido, após conferência: “Recebi. Vou conferir o horário com a equipe.” Não alterar a agenda automaticamente."
    if kind in {"emoji_text", "reaction"}:
        return "Reconhecer o retorno somente no contexto; não confirmar agenda sem visita segura."
    if kind == "sticker":
        return "Figurinha sem interpretação automática. Conferir o contexto antes de responder."
    if status == "human_hold":
        return "Manter o atendimento humano pausado e conferir o histórico antes de qualquer resposta."
    return "Revisar o contexto e a visita antes de responder. Não confirmar, cadastrar ou enviar automaticamente."


def render_note(status: str, kind: str, disposition: str, marker: str, context: list[dict] | None = None) -> str:
    if context:
        facts = "; ".join(
            "Nome: {name}. Visita: {date} às {time}. Serviço: {service}.".format(
                name=x.get("name") or "a identificar",
                date=x.get("date") or "a identificar",
                time=x.get("time") or "a identificar",
                service=x.get("service") or "a identificar",
            ) for x in context
        )
    else:
        facts = "Identidade ou visita ainda não comprovada; não inferir dados."
    return "Triagem humana — retorno WhatsApp retido. " + facts + " " + suggested_action(status, kind, disposition) + " Origem e conteúdo original permanecem no registro privado. " + marker


def make_case(row: sqlite3.Row) -> Case:
    status = row["status"]
    if status not in ALLOWED:
        raise ValueError("ineligible_status")
    origin_hash = _origin(row["address_hashes"])
    marker = "JP-TRIAGEM-CAPTURA-" + sha256(f"{row['capture']}:{row['message_id']}".encode()).hexdigest()
    note = render_note(status, row["kind"], row["disposition"], marker)
    return Case(row["capture"], marker, origin_hash, status, row["kind"], row["disposition"], row["received_at"], row["message_id"], row["content_sha256"], note)


def setup(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS triage_cases(
          capture INTEGER PRIMARY KEY, marker TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
          claimed_at INTEGER NOT NULL, conversation INTEGER, note INTEGER, result TEXT
        )"""
    )
    db.execute("CREATE TABLE IF NOT EXISTS triage_watermark(slot INTEGER PRIMARY KEY, capture INTEGER NOT NULL)")
    db.execute(
        "INSERT OR IGNORE INTO triage_watermark(slot,capture) SELECT 1,COALESCE(MAX(capture),0) FROM core_links"
    )
    db.commit()


def _claim(db: sqlite3.Connection, now: int) -> Case | None:
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM triage_cases WHERE state='claiming' AND claimed_at<?", (now - STALE_CLAIM_SECONDS,))
        watermark, = db.execute("SELECT capture FROM triage_watermark WHERE slot=1").fetchone()
        row = db.execute(
            """SELECT c.rowid AS capture,c.message_id,c.content_sha256,c.address_hashes,c.kind,c.disposition,c.received_at,l.status
                 FROM captured c JOIN core_links l ON l.capture=c.rowid
                 LEFT JOIN triage_cases t ON t.capture=c.rowid
                 WHERE l.status IN ('identity_review','visit_review','human_review','human_hold','captured')
                   AND t.capture IS NULL
                   AND c.rowid>?
                 ORDER BY c.seq,c.rowid LIMIT 1"""
            , (watermark,)
        ).fetchone()
        if row is None:
            db.commit()
            return None
        case = make_case(row)
        db.execute("INSERT INTO triage_cases(capture,marker,state,claimed_at) VALUES(?,?,?,?)", (case.capture, case.marker, "claiming", now))
        db.commit()
        return case
    except Exception:
        db.rollback()
        raise


def run_tick(db: sqlite3.Connection, deliver: Callable[[Case], tuple[int, int]], *, now: int | None = None, limit: int = 8) -> dict:
    """Deliver up to ``limit`` private cases and retain exactly one result per capture."""
    setup(db)
    now = int(time.time()) if now is None else now
    created = existing = 0
    for _ in range(limit):
        case = _claim(db, now)
        if case is None:
            break
        try:
            conversation, note = deliver(case)
        except Exception:
            with db:
                db.execute("DELETE FROM triage_cases WHERE capture=? AND state='claiming'", (case.capture,))
            raise
        with db:
            old = db.execute("SELECT state FROM triage_cases WHERE capture=?", (case.capture,)).fetchone()
            if old is None or old[0] != "claiming":
                raise RuntimeError("triage_claim_lost")
            db.execute(
                "UPDATE triage_cases SET state='created',conversation=?,note=?,result=? WHERE capture=?",
                (conversation, note, json.dumps({"status": case.status}, sort_keys=True), case.capture),
            )
        created += 1
    total = db.execute("SELECT count(*) FROM triage_cases WHERE state='created'").fetchone()[0]
    return {"created": created, "existing": existing, "total": total}
