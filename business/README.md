# Agenda e Clube: coordenador transacional de laboratório

`transactions.py` é código utilizável com adaptador injetado, não um transporte
AppBarber. `python -m unittest business.test_transactions -v` executa as provas
fictícias sem rede. Nenhum atendimento real, agendamento, adesão ou cobrança é
realizado por este pacote. Os workflows existentes não foram conectados a ele.

## API e persistência

Instancie `Coordinator(path, adapter)` com arquivo persistente em diretório
privado já existente, pertencente ao usuário do processo e com modo 0700. Use `execute(event_id, operation_id, request)` e retenha os dois IDs
originais em todos os reprocessamentos. `request` contém tenant/client IDs,
`action` (`create`, `change`, `cancel`, `join_club`), `payload` de campos exatos e
`consent`. O consentimento deve vir do sistema confiável de atendimento após
aceite explícito do cliente, nunca ser criado pelo modelo como se fosse evidência.
Ele contém `explicit: true`, `evidence_id` e `request_digest`, SHA-256 do JSON
canônico dos quatro campos anteriores, calculado com `digest`.

Os exemplos completos de requisição estão na função `request` dos testes. São
fictícios. O hash vincula o aceite ao conteúdo, mas não autentica o autor: autenticação,
autorização do atendente/cliente, captura e guarda da evidência são responsabilidades
do chamador confiável. Este módulo não é um endpoint público.

O ledger armazena a requisição mínima, digest, IDs, estado e motivo genérico, em
SQLite com modo 0600, transações e `synchronous=FULL`. O arquivo é aberto com
`O_NOFOLLOW`; caminhos com symlinks, hardlinks, FIFO e arquivos/diretórios com
permissões inseguras são rejeitados sem chmod. Cada conexão revalida diretório,
inode do banco e sidecars `-journal`, `-wal`, `-shm`. Nenhum arquivo suspeito é
removido ou reparado automaticamente. Ancestrais compartilhados graváveis só são
aceitos com proprietário root e sticky bit, como `/tmp`. O isolamento pressupõe
que outros processos do mesmo UID e root sejam confiáveis; não pretende proteger
contra um invasor com a mesma identidade do processo trocando caminhos durante
uma chamada SQLite. Não armazena cookies, nomes,
telefones, mensagens completas, respostas cruas nem erros do provedor. Não exporte
esse banco como evidência pública. Backups/retencão e segurança do host precisam
ser definidos antes de qualquer uso fora do laboratório.

Estados: `blocked` não despachou; `in_flight` já está persistido e pode ter sido
despachado; `unknown` requer reconciliação; `confirmed` tem prova de releitura;
`rejected` é terminal e indica nenhuma mutação aplicada. IDs repetidos com payload
ou evento diferente falham. Duplicatas idênticas devolvem o estado armazenado,
sem repetir escrita. Estado terminal nunca regride por leitura concorrente.

Locks persistentes por cliente e por agendamento bloqueiam nova operação enquanto
há tentativa incerta, inclusive se outra requisição usar um ID diferente.
Uma tentativa já confirmada seguida de evento realmente novo pode representar
outro pedido legítimo; a camada de ingestão deve preservar o ID canônico do evento,
e o adaptador deve aplicar restrições comerciais de duplicação de reservas/adesões.

`reconcile(operation_id)` só lê a operação original e, quando conhecido, o ID
exato da entidade. Não procura registros semelhantes nem libera locks por ausência
de prova. Timeout, processo morto ou erro após despacho não geram retry. Não há
limpeza automática, reenvio, liberação manual de locks ou operação de pagamento.
`SessionExpired` e `Rejected` só podem ser lançados pelo adaptador quando ele sabe
que não houve escrita. Expiração detectada apenas após despacho incerto deve ser
exceção comum, não `Rejected`.

## Contrato obrigatório do adaptador

Por padrão falha fechado. Exige `Contract` validado, versionado, exclusivamente
`laboratory`, com `atomic_preconditions` e `operation_lookup`. Um booleano de
configuração não é prova: esses atributos são declarações de código confiável,
que precisam ser auditadas contra a implementação do adaptador.

1. `inspect(request)`: consulta fresca, propriedade tenant/cliente, revisão,
   identidade do agendamento e disponibilidade. O coordenador compara identidade
   e revisão previamente consentida para alteração/cancelamento.
2. `mutate(operation_id, request, revision)`: aplica no máximo uma mutação com
   precondições atômicas de propriedade/revisão e exclusão de conflito de horário.
   Deve retornar ID da entidade ou falhar com semântica explícita. Se não puder
   garantir essas condições, não deve declarar o contrato validado.
3. `observe(operation_id, entity_id)`: devolve prova durável da mesma operação,
   mesma entidade quando conhecida, tenant/cliente/action e digest do payload
   efetivamente aplicado. ACK de requisição aceita não satisfaz essa prova.

Clube exige plano, versão e digest das condições, valor em centavos, BRL,
modalidade (`monthly`, `annual`, `one_time`), `enrollment_only: true` e aceite
explícito vinculado a tudo isso. A consulta precisa devolver exatamente o mesmo
plano e `charges_on_enrollment: false`. Condições ausentes/alteradas, cobrança
imediata ou desconhecida bloqueiam. O módulo não contém cobrança nem aceite de
contrato separado. O digest deve representar condições completas aprovadas,
incluindo inclusões, recorrência, cancelamento e reembolso, não só um nome de plano.

## Descoberta AppBarber e lacunas de integração

A inspeção somente leitura em 09/11/2026 identificou código existente no projeto
Mac `jp-protese-capilar`, em `scripts/jp-appbarber-new-visit.mjs`,
`scripts/jp-appbarber-confirmation.mjs` e
`docs/appbarber-criacao-visita-contrato.md`:

- Criação: POST `/pages/cadastros/insereAgendamentov5.php`; abre também comanda
  vinculada. Não implica fechar comanda, cobrar ou autorizar esses efeitos.
- Mudança: POST `/pages/cadastros/alteraAgendamento.php`; exige item vinculado,
  data/hora e profissional, com releitura obrigatória.
- Cancelamento: POST `/pages/actions/cancelaHorario.php`; helper descoberto é
  limitado a teste controlado com prova de criação, não cancelamento genérico.
- Leitura de agenda: POST `/pages/actions/buscaAgenda3.php`; identidade, serviço,
  duração e status precisam ser confrontados. Offset retornado pode divergir
  do horário literal local; não converter silenciosamente.

Não há prova de idempotência nativa, comparação-e-troca de revisão ou exclusão
atômica de horários nesses endpoints. Portanto não existe adaptador certificado
para este coordenador. Não se deve habilitar flags só porque os caminhos foram
encontrados. Contrato real de adesão Clube e suas condições também não foram
identificados. Uma futura integração exige resolver essas lacunas ou desenvolver
outro protocolo explicitamente revisado, mais conservador, sem alegar garantias
inexistentes. Não foram feitas chamadas autenticadas nem escritas de teste real.

## Alcance dos testes

As fixtures exercitam a máquina de estados real e o banco real em arquivos
SQLite temporários: create/change/cancel/join, indisponibilidade, conflito atômico
após consulta, duplicatas, 20 chamadas concorrentes, bloqueio por recurso,
conflitos de IDs, reinício, interrupção abrupta, timeout antes/depois da escrita,
reconciliação exata, leitura divergente, sessão expirada, consentimento, condições
Clube ausentes/alteradas e bloqueio de cobrança. Isso prova o coordenador diante
desse contrato sintético, não comportamento de servidor AppBarber, integração
WhatsApp, emissão de recibos reais ou funcionamento em produção.

Regressões de armazenamento cobrem symlink de banco sem alteração do alvo,
symlinks dos três sidecars preservados, hardlink, diretório não privado, diretório
symlink, arquivo não privado sem chmod, troca de inode e FIFO sem bloqueio.
