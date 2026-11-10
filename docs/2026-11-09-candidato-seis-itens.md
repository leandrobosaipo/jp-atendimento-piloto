# Candidato offline dos seis itens JP

Esta etapa implementa componentes e testes de laboratório, não uma release de
produção. Não houve alteração da instalação Dell, canal real, Evolution/Mac Mini,
AppBarber, adesão, membros do grupo ou permissões. Os programas existentes continuam
sem importar os novos módulos. Não há instalador ou caminho de ativação do candidato.

## Resultado verificável por item

1. **WhatsApp, seis imagens e Instagram:** `channels/` testa entrada autenticada,
   vínculo de conta/destinatário, reserva das seis variantes, mensagens curtas
   ordenadas e confirmação de entrega. O transporte é fictício. Faltam imagem real
   no WhatsApp, conta/conector Meta, mídia e resposta Instagram ponta a ponta.
2. **Agenda:** `business/` testa criar, alterar e cancelar por adaptador injetado,
   consentimento vinculado, exclusão de concorrência, conflitos e releitura. Os
   contratos reais existentes foram documentados, mas não executados. AppBarber
   não comprova precondição atômica/lookup de operação exigidos pelo candidato;
   portanto o candidato bloqueia produção em vez de prometer garantia inexistente.
3. **Clube:** condições versionadas, custo, modalidade e consentimento obrigatórios;
   mudança das condições ou cobrança na adesão bloqueia a operação. Ainda falta
   definir o significado da adesão e condições comerciais. Nenhuma inscrição real.
4. **Exceções e atendentes:** resumo mínimo e gate de grupo verificado, somente
   exceções. Confirmação básica de agenda não encaminha. Não há envio nem criação
   de acesso; faltam lista atual de identidades/permissões e conferência do grupo.
5. **Retenção, backup e alertas:** `operations/` planeja corte de um ano por
   calendário, protege holds/estados incompletos, copia SQLite consistente e restaura
   em arquivo novo. Alertas locais sobre integridade/idade. Sem purge ou timer.
   Cobertura não inclui PostgreSQL, anexos, backup externo ou restauração completa.
6. **Revisão/virada:** regressão local e workflow do candidato; nenhuma virada.
   O serviço existente deve permanecer dono único dos eventos até release exata,
   contratos e readbacks reais, cópias verificadas e retorno de código definidos.

## Evidência fictícia e reprodução

- `channels/test_bridge.py`: duas respostas, aceite → bloqueio da segunda parte →
  recibo vinculado → segunda resposta → conclusão. Timeout na primeira tentativa,
  reinício e nova entrada continuam retidos sem segundo envio. Seis workers
  concorrentes realizam um único envio. Seis variantes são escolhidas em fixtures
  e o replay conserva o mesmo plano. HMAC inválido, conta/destino errado, conteúdo
  diferente e recibo somente `sent` nunca confirmam entrega.
- `business/test_transactions.py`: criação, mudança, cancelamento e inscrição
  fictícios confirmados por releitura; slot ocupado, sessão expirada, ausência de
  consentimento e alteração comercial não avançam. Timeout não é repetido.
- `operations/test_retention.py`: SQLite com WAL confirmado, hashes, restauração
  nova, corrupção detectada, corte em ano bissexto e registros protegidos.

Os testes usam somente dados inventados. Não constituem evidência de atendimento
real, entrega WhatsApp ou reserva/adesão no AppBarber. A CI integral do piloto segue
separada da suíte offline e deve ser consultada no SHA exato do candidato.

## Pendências para avançar sem improvisar decisões

- Identificar conta Instagram/Meta e acesso necessário; aprovar separadamente
  eventual concessão de acesso persistente.
- Definir destinatário de homologação e registros de teste autorizados no AppBarber,
  com serviços, profissional e horários controlados. Não usar registro histórico
  como autorização automática para novo teste ou comanda.
- Confirmar planos/termos: efeito da adesão, cobrança, renovação, validade e
  cancelamento. O preço da arte não responde a essas condições.
- Confirmar grupo/membros e identidades dos atendentes, com menor acesso necessário.
- Definir marco/abrangência da retenção, cópias externas e recuperação completa.
  Qualquer exclusão permanente exige confirmação própria e não está implementada.
- Capturar runtime real e árvore de trabalho atual antes de preparar deploy.
  O projeto de produção tem mudanças locais e histórico de releases divergentes;
  não sobrescrever a árvore nem restaurar banco antigo sobre eventos novos.
