import {createCipheriv,createDecipheriv,randomBytes} from 'node:crypto';
import {gzipSync,gunzipSync,constants} from 'node:zlib';

export const LEGACY_FORMAT='jp-state-aes-256-gcm-v1';
export const COMPACT_FORMAT='jp-state-aes-256-gcm-v2';
export const MAX_PLAIN_BYTES=256*1024*1024;
export function isEncryptedEnvelope(value){return value?.format===LEGACY_FORMAT||value?.format===COMPACT_FORMAT;}
function keyCheck(key){if(!Buffer.isBuffer(key)||key.length!==32)throw Error('encrypted_state_key_invalid');}
function aad(length){return Buffer.from(`${COMPACT_FORMAT}:gzip:${length}`,'utf8');}
export function encodeStateEnvelope(serialized,key,{compact=true}={}){
  keyCheck(key);
  const plain=Buffer.from(serialized,'utf8');
  if(plain.length>MAX_PLAIN_BYTES)throw Error('encrypted_state_too_large');
  const compressed=compact&&plain.length>=4096?gzipSync(plain,{level:6}):null;
  const useCompact=compressed!==null&&compressed.length<plain.length*0.95;
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);
  if(useCompact)cipher.setAAD(aad(plain.length));
  const ciphertext=Buffer.concat([cipher.update(useCompact?compressed:plain),cipher.final()]);
  return {format:useCompact?COMPACT_FORMAT:LEGACY_FORMAT,...(useCompact?{compression:'gzip',plain_bytes:plain.length}:{}),iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64'),ciphertext:ciphertext.toString('base64')};
}
export function decodeStateEnvelope(stored,key){
  keyCheck(key);
  if(!isEncryptedEnvelope(stored))throw Error('encrypted_state_format_unsupported');
  const compact=stored.format===COMPACT_FORMAT;
  if(compact&&(stored.compression!=='gzip'||!Number.isSafeInteger(stored.plain_bytes)||stored.plain_bytes<1||stored.plain_bytes>MAX_PLAIN_BYTES))throw Error('encrypted_state_encoding_invalid');
  const iv=Buffer.from(stored.iv,'base64'),tag=Buffer.from(stored.tag,'base64');
  if(iv.length!==12||tag.length!==16)throw Error('encrypted_state_auth_invalid');
  const decipher=createDecipheriv('aes-256-gcm',key,iv);
  if(compact)decipher.setAAD(aad(stored.plain_bytes));
  decipher.setAuthTag(tag);
  const decrypted=Buffer.concat([decipher.update(Buffer.from(stored.ciphertext,'base64')),decipher.final()]);
  const plain=compact?gunzipSync(decrypted,{maxOutputLength:stored.plain_bytes}):decrypted;
  if(compact&&plain.length!==stored.plain_bytes)throw Error('encrypted_state_length_invalid');
  return plain.toString('utf8');
}
