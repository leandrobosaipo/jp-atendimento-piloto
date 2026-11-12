# Encaixe no estágio `4414148`

Não aplique enquanto o runtime não estiver cercado e a revisão estiver aprovada.

1. Copie para `/ops-app/`: `autonomous_same_day_reschedule.mjs`,
   `captured_same_day_reschedule.mjs`, `reschedule_permits.py`, e as versões
   correspondentes de `task_reads.mjs` e `effect_client.mjs` deste commit.
2. No handler já autenticado de `reply-execute`, após
   `runCapturedConfirmation(...)` e antes do ACK/outbox, execute
   `prepareCapturedSameDayReschedule(capturedVisitHelpers)`.
3. O lado de captura chama `reschedule_permits.issue(...)` somente para a
   proposta `grant_required`, com `latest_is_source` verificando cursor,
   identidade PN/LID, ausência de mensagem posterior e ausência de intervenção
   humana. O emissor não envia mensagens nem chama AppBarber.
4. No próximo tick, somente com grant presente, envolva
   `moveOfficialAppointment` em `withReplyEffect(task, load, ...)`; a escrita
   passa por `effectFetch`, `nativeScope`, `approval` e `guardedPost`. Releia
   antes/depois; timeout permanece `write_unknown` e não faz retry.
5. Publique o feedback apenas como item da outbox existente depois da releitura
   confirmar ID, cliente, serviço, profissional, duração e horário alvo. O
   transportador único entrega esse item; aceite não é entrega.

Retorno: desative `automatic-reschedules.json`; grants expiram em no máximo
quatro minutos. Preserve intents/observed para reconciliação; não restaure
estado anterior sobre a nova tentativa.
