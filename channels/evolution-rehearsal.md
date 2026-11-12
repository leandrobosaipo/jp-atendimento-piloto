# Ensaio de transporte Evolution

A candidata acrescenta uma fronteira de transporte ao SQLite de `channels/bridge.py`. O executor injeta uma troca em memória e uma releitura independente de mensagens fictícias. O módulo usa Python porque o contrato de eventos, a fila e o controle de transações já usam a biblioteca padrão dessa linguagem. Nenhum executável desta candidata aceita endereço de servidor, token, QR ou arquivo de sessão.

| Recurso | O que o ensaio verifica |
|---|---|
| Comando de grupo | Envelope autenticado pelo chamador, instância e JID do grupo esperados, direção de entrada, participante identificado, presença nas listas permitida e conferida, citação de alerta no mesmo caso e limite temporal. LID é comparado como identidade opaca. O resultado é uma proposta de ação local, com deduplicação persistente. |
| Texto e JPEG | O transporte monta os campos de `sendText` e `sendMedia`. Compara os bytes do JPEG com o SHA reservado antes da troca e na releitura. A implementação da Central capturada usa um transporte de vídeo. O teste de JPEG valida a candidata com dados fictícios. |
| Aceite | Uma resposta bem-sucedida exige ID nativo, destino e direção compatíveis. O aceite mantém a próxima parte bloqueada. |
| Entrega e leitura | Um callback fictício só libera a parte quando instância, destino, ID e participante esperado coincidem e a releitura externa do conteúdo confere. O estado de leitura é monotônico por observador. Um recibo de participante não comprova leitura por todos os membros. |
| Resultado incerto | Se o transporte perder a confirmação depois da troca, mantém a parte em envio incerto. O reinício preserva o bloqueio da parte, da cauda e de novas mensagens daquela conversa. Um callback compacto sem destino ou a ausência de callback mantém a pendência. |
| Troca de dono | A reserva e a chamada passam pela mesma trava durante a troca. O congelamento espera o envio em curso. A nova geração invalida a anterior e a transferência recusa partes ainda incertas ou aceitas sem confirmação. Isso exige que todos os participantes usem o mesmo ledger e a mesma trava. |

A CI usa um namespace de rede Linux novo, sem interface externa ou rota. O ensaio pode ser executado em outro diretório, com uma conta sem privilégios, arquivos SQLite temporários e sem configuração do serviço instalado.

```sh
sudo unshare --net -- setpriv --reuid="$(id -u)" --regid="$(id -g)" --clear-groups --no-new-privs env -i PATH=/usr/bin:/bin REQUIRE_NETWORK_ISOLATION=1 python3 -B channels/test_evolution.py
```

O simulador produz callbacks completos para testar o consumidor. O handler compilado `message-receipt.update` capturado da imagem em uso produziu zero webhooks no teste isolado anterior. O ensaio não estabelece que o provedor emita esses callbacks, que exista releitura autenticada para o JPEG ou que o processo do Mac Mini respeite a trava local. A configuração e os recibos do piloto Chatwoot também continuam independentes.

Antes de qualquer corte, a equipe precisa demonstrar o caminho efetivo de recibo e releitura para o destino exato, incluindo o que significa entrega em grupo, validar os participantes e o alerta citado, reconciliar partes incertas e verificar o bloqueio do escritor antigo na fronteira que realmente envia. A restauração de backups e o retorno precisam preservar os eventos aceitos após a fotografia inicial. Essas verificações pertencem ao pacote privado de migração.
