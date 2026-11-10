# Fronteiras dos canais e caixa de saída: candidato offline

## Journal de entrada para futuro receptor Dell

`ingress.Journal` reutiliza o SQLite privado do `Outbox` e os mesmos eventos
normalizados, sem duplicar payloads. A implementação permanece em Python para
reutilizar esse contrato e a transação existente; introduzir Rust nesta fronteira
agora criaria uma segunda implementação e migração sem benefício demonstrado.

Evolution exige comparação constante do segredo privado (mínimo 32 bytes) antes
da normalização; Instagram reutiliza HMAC dos bytes originais. O futuro receptor
deve extrair a credencial do cabeçalho autenticado e fornecer configuração privada,
nunca confiar em flags do JSON. Não existe servidor HTTP ou configuração de canal.

O retorno `committed_locally` só ocorre depois do commit SQLite com
`synchronous=FULL`. Falha de commit propaga erro, sem ACK. Replay mantém a mesma
sequência e identidade; alteração do mesmo ID bloqueia e reverte todo o lote.
O journal não apaga eventos. `high_water()` captura o limite e
`read(after=..., through=..., limit=...)` pagina até esse limite, excluindo novas
entradas. Sequências podem ter lacunas; avance pelo último valor lido, sem presumir
contiguidade. Leitura inclui texto privado: somente consumidores locais autorizados.

Execute `python3 channels/test_ingress.py` (no macOS, use um `TMPDIR` sem symlinks,
como `/private/tmp`). Fixtures cobrem processo novo, replay, conflito, concorrência,
autenticação, lote atômico e falha transacional. Não comprovam perda elétrica,
restauração do disco Dell, ACK HTTP real ou integração com n8n. Apenas os tipos de
texto suportados pelos normalizadores são aceitos; grupos, mídia, recibos e demais
callbacks precisam de contrato próprio antes de substituir o webhook atual.

Não há consumo automático, confirmação de visita, cursor persistido do consumidor,
backfill de eventos antigos, fila de processamento nem envio. A futura integração
deve manter processamento idempotente e ACK HTTP somente após este retorno; um ACK
perdido deve reconciliar o mesmo ID. Publicação desta fonte não autoriza ativação.

Este módulo não instala servidor, callback, rota, segredo ou transporte. Não está
ligado à Evolution, Instagram, Chatwoot ou atendimento atual. O n8n continua sendo
o único dono do recebimento e envio da operação existente no Mac Mini.

## O que foi implementado e testado

- Entrada normalizada WhatsApp com instância explícita, autenticação já realizada
  pelo chamador, ID nativo, mensagem direta e rejeição de eco/grupo/LID.
- Entrada de texto Instagram com HMAC SHA-256 sobre bytes originais, conta
  destinatária verificada, rejeição de mídia não suportada e ignorar eco/recibos.
- SQLite privado com identidade canal/conta/evento e conflito de payload bloqueado.
- Plano persistido em mensagens de até 250 caracteres, negrito simples e imagem
  separada. O formatador existente `integration.service.message_parts` continua
  sendo a fonte da divisão; `Outbox.prepare` recebe partes já formatadas.
- Três variantes por grupo, reserva persistida e preservada no replay.
  Testes demonstram as seis posições do manifesto fictício, não bytes das artes
  reais nem transmissão WhatsApp. Não há upload de imagem neste módulo.
- Envio ordenado injetável. Aceite não é entrega. Próxima parte só pode sair após
  recibo nativo de entrega/leitura vinculado ao ID, conta, destinatário e digest
  do conteúdo. Hash de imagem faz parte do payload; a implementação do transporte
  deve verificar bytes antes do envio e resolver o recibo nativo correspondente.
- Timeout/ack perdido deixa `sending` permanente até revisão; não há retry
  automático nem inferência por texto parecido. Novos eventos da conversa ficam
  retidos. Um ID nativo já usado não pode confirmar outra parte.
- Resumo mínimo para exceções verdadeiras. Confirmação de agenda não é exceção.
  Grupo exige identidade/membros autorizados e conferência recente; essa checagem
  recebe fatos verificados, não os descobre nem concede acesso.

Execute `python3 channels/test_bridge.py` a partir da raiz. Transporte e verificador
nos testes são fixtures, sem rede. Flags como `authenticated=True` e configuração
verificada são contratos entre código confiável, não mecanismos de autenticação
expostos a clientes. Um futuro servidor deve atribuí-las, nunca copiar do webhook.

## Contratos reais consultados, sem executar chamadas

Fontes locais da operação existente `jp-protese-capilar`:

- `scripts/jp-n8n-migration-workflows.mjs`: webhook POST `jp-inbound-n8n-v1`,
  `headerAuth`, encaminhamento para `/api/n8n/inbound`, execução/workflow explícitos,
  sem repetição automática. A rota não deve ser apontada para este candidato.
- `scripts/jp-delay-support.mjs`: instância exata, `messages.upsert`/`messages.update`,
  normalização de dados, remetente, ID e direção; LID não é telefone.
- `scripts/jp-evolution-local-api.mjs`: `POST /message/sendText/{instance}`,
  `number`, `text`, `linkPreview:false`; `key.id` significa aceite, não entrega.
- `scripts/jp-brand-adapter.mjs`: mídia existente é vídeo. A descoberta não encontrou
  homologação `sendMedia` para as seis imagens; não assumir suporte validado.
- `scripts/jp-test-evolution-group-receipts.mjs`: há eventos de grupo sem destinatário.
  Um futuro verificador não pode atribuir entrega somente por ID semelhante.

O callback atual tem capacidades adicionais, incluindo grupos e mensagens de mídia.
Este normalizador é deliberadamente menor e não pode substituí-lo integralmente.
Não há conta Meta identificada/conectada, envio Instagram, resposta a desafios de
assinatura ou configuração de permissões. HMAC testado não significa webhook instalado.

## Antes de usar canal real

Precisamos de conta/instância atual verificadas, destino autorizado para homologação,
manifesto das seis imagens e bytes íntegros, política de mensagens Meta e permissões
necessárias aprovadas, implementação de transporte e recibo nativo, teste ponta a
ponta e revisão de concorrência com o remetente atual. Não criar segundo remetente.
A ativação e configuração de acesso persistente são etapas próprias e exigem as
aprovações aplicáveis. Nenhuma mudança em membros/grupos/permissões foi realizada.
