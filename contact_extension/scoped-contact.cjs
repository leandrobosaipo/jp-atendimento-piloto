'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const digest = data => crypto.createHash('sha256').update(data).digest('hex');
const now = () => Math.floor(Date.now() / 1000);
const held = () => { throw Error('permission_held'); };
const requireTrue = value => { if (!value) held(); };
const production = { permits: '/run/contact-permits', ledger: '/var/lib/contact-intents', permitOwner: 0 };
const validName = name => name === null || (typeof name === 'string' && name.length <= 100 && name === name.trim());

// This canonicalization is shared with the host and approval tool. The contact request accepts only strings and null.
function canonical(value) {
  if (value === null || typeof value === 'string') return JSON.stringify(value);
  requireTrue(value && typeof value === 'object' && !Array.isArray(value));
  return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
}
function readPermit(filename, uid) {
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const st = fs.fstatSync(fd);
    requireTrue(st.isFile() && st.uid === uid && !(st.mode & 0o027) && st.size <= 4096);
    return JSON.parse(fs.readFileSync(fd, 'utf8'));
  } finally { fs.closeSync(fd); }
}
function claim(file, data) {
  const fd = fs.openSync(file, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(data)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  const dir = fs.openSync(path.dirname(file), 'r');
  try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
}
function permitted(body, auth, cfg) {
  requireTrue(auth && /^[a-f0-9]{64}$/.test(auth.task) && /^[a-f0-9]{64}$/.test(auth.secret));
  const st = fs.lstatSync(cfg.permits);
  requireTrue(st.isDirectory() && st.uid === cfg.permitOwner && !(st.mode & 0o022));
  const grant = readPermit(path.join(cfg.permits, auth.task + '.json'), cfg.permitOwner);
  requireTrue(grant.task === auth.task && grant.schema === 1 && grant.role === 'registration' && grant.operation === 'contact');
  requireTrue(typeof grant.operator === 'string' && grant.operator.trim() && typeof grant.cacheExists === 'boolean');
  requireTrue(Number.isInteger(grant.issued) && Number.isInteger(grant.until) && grant.issued <= now() &&
    now() < grant.until && grant.until - grant.issued <= 300);
  requireTrue(/^[a-f0-9]{64}$/.test(grant.secretSha) &&
    crypto.timingSafeEqual(Buffer.from(digest(auth.secret),'hex'), Buffer.from(grant.secretSha,'hex')));
  requireTrue(grant.bodySha === digest(canonical(body)));
  const proof = body.sourceProof;
  requireTrue(typeof body.instance === 'string' && /^[1-9][0-9]{7,14}$/.test(body.phone) && validName(body.name) && body.name &&
    validName(body.expectedPreviousName) && validName(body.expectedCacheName ?? null) && body.expectedPreviousName !== body.name);
  requireTrue(proof && proof.source === 'whatsapp_contact_screen_human' && proof.phone === body.phone &&
    proof.name === body.expectedPreviousName && typeof proof.proofId === 'string' && proof.proofId.trim() &&
    Date.parse(proof.observedAt) <= Date.now() && Date.now() - Date.parse(proof.observedAt) <= 300000);
  return grant;
}
async function route(req, res, controller, validHumanProof) {
  const body = req.body;
  if (!body || !validHumanProof(body.sourceProof, body.phone, body.expectedPreviousName))
    return res.status(428).json({ error:'authoritative_human_source_proof_required' });
  try {
    const result = await controller.writeJpContactName(body.instance, {body, auth:{
      task:req.get('x-contact-task'), secret:req.get('x-contact-permit')
    }});
    if (result.status !== 'accepted_unverified') return res.status(504).json({status:'write_outcome_unknown',retryable:false});
    return res.status(200).json({status:result.status,providerAck:true,readback:{source:'evolution_db_cache',authoritative:false,matched:true}});
  } catch (error) {
    if (error.message === 'permission_held' || error.code === 'ENOENT') return res.status(423).json({error:'contact_write_held',retryable:false});
    return res.status(504).json({status:'write_outcome_unknown',retryable:false});
  }
}
async function write(service, {body,auth}, nativeJid, cfg=production) {
  const grant = permitted(body, auth, cfg);
  requireTrue(service.instance.name === body.instance);
  const jid = nativeJid(body.phone);
  requireTrue(jid === body.phone + '@s.whatsapp.net');
  const keyStore = service.instance.authState.state.keys;
  const lid = (await keyStore.get('lid-mapping',[body.phone]))?.[body.phone];
  requireTrue(typeof lid === 'string' && /^[0-9]+$/.test(lid));
  const reverse = lid + '_reverse';
  requireTrue((await keyStore.get('lid-mapping',[reverse]))?.[reverse] === body.phone);
  const db = service.prismaRepository.contact;
  const where = {remoteJid:jid,instanceId:service.instanceId};
  const lookup = () => db.findUnique({where:{remoteJid_instanceId:where},select:{pushName:true}});
  const expectedCache = Object.hasOwn(body,'expectedCacheName') ? body.expectedCacheName : body.expectedPreviousName;
  const matches = row => Boolean(row) === grant.cacheExists && (row?.pushName ?? null) === expectedCache;
  const prefix = path.join(cfg.ledger, auth.task);
  // Even a lost HTTP response after a completed write must not initiate another mutation.
  if (fs.existsSync(prefix+'.intent')) return {status:'write_outcome_unknown'};
  requireTrue(matches(await lookup()));
  const recipient = digest(canonical({instance:body.instance,phone:body.phone}));
  claim(path.join(cfg.ledger,recipient+'.lock'), {task:auth.task});
  claim(prefix+'.intent', {task:auth.task,bodySha:grant.bodySha,at:now()});
  try {
    requireTrue(matches(await lookup()));
    const fresh = permitted(body, auth, cfg);
    requireTrue(fresh.cacheExists === grant.cacheExists && fresh.issued === grant.issued && fresh.until === grant.until);
    await service.client.addOrEditContact(jid,{fullName:body.name,firstName:body.name,lidJid:lid+'@lid'});
    if (grant.cacheExists) {
      requireTrue((await db.updateMany({where:{...where,pushName:expectedCache},data:{pushName:body.name}})).count === 1);
    } else { await db.create({data:{...where,pushName:body.name}}); }
    requireTrue((await lookup())?.pushName === body.name);
    claim(prefix+'.observed', {task:auth.task,providerAck:true,cacheMatched:true,consumerVerified:false});
    return {status:'accepted_unverified'};
  } catch { return {status:'write_outcome_unknown'}; }
}
module.exports={write,route,canonical,digest};
