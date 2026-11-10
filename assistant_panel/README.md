# Conversa privada de teste

No Cockpit, abra **Teste de atendimento**. Escreva uma situação fictícia. Cada resposta mostra os títulos usados da base, a decisão de atendimento ou encaminhamento, tokens e estimativa em dólares. Uma nova conversa limpa a tela e muda a sessão. A cota continua compartilhada. O acesso exige o login do painel e autorização no socket Unix.

Um serviço do próprio convidado seleciona até quatro trechos da base privada, inclui até três trocas anteriores da sessão e chama exclusivamente a API Responses. Usa gpt-6-luna em tarifa Standard, raciocínio none, limite de 600 tokens de saída e store=false. Não disponibiliza ferramentas ao modelo. O painel é independente dos fluxos sintéticos existentes. A sugestão de encaminhamento fica na conversa de teste.

A cota SQLite reserva antes da chamada. O limite desta aplicação é US$ 0,05 por dia UTC e US$ 0,25 no ensaio inteiro, com 64 tentativas diárias e oito por sessão. Cada tentativa reserva US$ 0,003. A resposta confirmada ajusta a reserva ao uso, com margem para cache. Um resultado incerto continua reservado. O identificador do pedido impede uma segunda chamada após perda da resposta. A estimativa usa preços públicos, não substitui a fatura e não limita outros usos da mesma chave. As conversas locais ficam disponíveis como contexto por até 24 horas e o conteúdo vencido é descartado na próxima pergunta.

A chave, a base e o histórico ficam fora do Git. O browser recebe a resposta e os títulos das fontes selecionadas. A chamada envia somente a pergunta, contexto curto e trechos selecionados ao destino fixo. O navegador não recebe segredos nem arquivo de base. A pilha anterior permanece em rede interna. As ferramentas nativas de Ubuntu, Cockpit e biblioteca padrão Python evitaram instalar toolchain e um servidor web adicional.

Para suspender a função, o operador pode parar o socket e o serviço `atendimento-piloto-ai`, preservando o histórico e os outros serviços. [Modelo e parâmetros oficiais](https://developers.openai.com/api/docs/models/gpt-6-luna).

## Candidata: orçamento mensal e avisos locais

O gateway acrescenta um teto local de **US$10 por mês UTC**, com marcos de **US$5 e US$8**, sem elevar as cotas do piloto: US$0,05/dia, US$0,25 no ensaio, 64 tentativas/dia e oito turnos por sessão. Os novos marcos não são atingíveis normalmente no piloto enquanto o teto menor do ensaio estiver ativo. Os testes mensais usam registros inteiramente sintéticos e elevam somente a constante do ensaio no processo de teste; não existe opção de alteração desses limites pela interface.

A contagem usa o mês UTC de admissão da tentativa, consistente com a cota diária atual. Antes da chamada, uma transação SQLite reserva o custo máximo e grava o aviso correspondente. O consumo confirmado ajusta a reserva; resultados pendentes/incertos de meses anteriores continuam retidos no orçamento disponível até reconciliação explícita. A virada do mês não apaga nem zera o ledger. Essa regra conservadora e a referência UTC devem fazer parte da revisão final de operação.

Os avisos são registros duráveis visíveis em **Teste de atendimento**, uma vez por marco por mês. Referem-se ao total comprometido, incluindo reservas, e podem permanecer mesmo se o valor cair após a resposta. Não são notificações por WhatsApp, e-mail ou grupo, não dependem de um destinatário e não acionam qualquer envio externo. O limite é desta aplicação, não é um limite da conta OpenAI nem garantia de valor da fatura ou de outros serviços. As tarifas e a margem de reserva também precisam de conferência antes da liberação real.

A tabela `budget_alerts` é aditiva; o formato e os registros de `turns` não são migrados nem apagados pela inicialização. A limpeza de contexto de 24 horas já existente permanece inalterada; esta etapa não implementa a decisão de retenção por um ano. O retorno para código anterior pode preservar o banco atualizado, mas desativa o novo bloqueio mensal e os avisos. Nunca restaurar um banco antigo sobre eventos novos. Em caso de interrupção, conferir a fonte instalada, os recibos do atualizador e a contagem/reservas antes de retomar.

Validação offline:

```sh
python3 assistant_panel/test_gateway.py
python3 assistant_panel/test_monthly_budget.py
python3 assistant_panel/test_update.py
node assistant_panel/test_panel.cjs
node --check assistant_panel/cockpit/panel.js
```

São cobertos: cap exato, concorrência, reinício, interrupção após reserva, falha ao gravar aviso, migração aditiva do ledger legado, replay, mudança de mês/ano, fuso UTC, reservas incertas anteriores e respostas locais gratuitas. O teste da interface usa DOM simulado, não comprova renderização no navegador. A publicação e a CI do SHA final são pré-requisitos de qualquer instalação. Nenhum teste offline comprova implantação no Dell, entrega no WhatsApp, escrita de agenda ou adesão real.


### Manutenção controlada: versão legada → orçamento mensal

Execute `update.py --maintenance --source-commit <HEAD-exato-limpo> --from-commit <SHA-instalado-do-painel> --machine-id <identidade-verificada>` primeiro sem `--apply`. A aplicação exige fonte publicada e os dois workflows aprovados no mesmo SHA. O caminho normal do atualizador continua disponível. A manutenção recusa alterações em `integration/` entre as revisões.

O bloqueio exclusivo já usado no banco de integração impede novas admissões. Eventos incompletos e pedidos de IA pendentes bloqueiam a atualização; reservas históricas `uncertain` estáveis são preservadas. Depois de uma janela sem atividade, são parados somente o serviço de integração e o socket/serviço de IA. Os dois bancos recebem cópias verificadas pela API de backup SQLite. São registrados hashes e contagens de eventos, partes e turnos, além das configurações privadas, credenciais, conhecimento, manifestos, HMAC e unidades/drop-ins. O código é gravado com `fsync`, mantendo inode e metadados. Não há chmod, alteração de ACL, proprietário ou configuração. Modos e proprietários dos sockets/diretórios de execução são verificados antes e depois; ACLs/xattrs não padronizados causam recusa.

A verificação usa apenas `info` local, sem chamada paga. Campos antigos de configuração permanecem iguais; contadores podem crescer com novos turnos após a reabertura. Todas as linhas anteriores precisam permanecer intactas. Mantenha o painel ocioso: uma expiração legítima que altere linhas antigas também causa recusa conservadora. Falhas revertem somente código, nunca os bancos vivos, novas linhas, esquema ou alertas. Serviços voltam ao estado original após sucesso ou reversão verificada. Reversão incompleta mantém a entrada parada e exige reconciliação.

Cada tentativa mantém evidências próprias: intenção, código anterior, backups, hashes e resultado/falha. `rollback.json` resolvido não avança a origem instalada nem bloqueia uma nova tentativa; recibo incompleto bloqueia novas atualizações. Se a publicação do ponteiro tiver resultado incerto, o recibo concluído e o ponteiro efetivamente gravado determinam se a operação já foi concluída. Backups não são restauração automática.

Somente `assistant-install/installed-current.json` avança. O recibo da integração mantém o SHA anterior porque seu código não mudou. Uma futura atualização da integração deve reconciliar explicitamente essa diferença, sem substituir o ponteiro do painel por uma versão antiga. O CI exercita a migração real da versão legada para o SHA candidato com serviços systemd e dados fictícios.
