# Conversa privada de teste

No Cockpit, abra **Teste de atendimento**. Escreva uma situação fictícia. Cada resposta mostra os títulos usados da base, a decisão de atendimento ou encaminhamento, tokens e estimativa em dólares. Uma nova conversa limpa a tela e muda a sessão. A cota continua compartilhada. O acesso exige o login do painel e autorização no socket Unix.

Um serviço do próprio convidado seleciona até quatro trechos da base privada, inclui até três trocas anteriores da sessão e chama exclusivamente a API Responses. Usa gpt-6-luna em tarifa Standard, raciocínio none, limite de 600 tokens de saída e store=false. Não disponibiliza ferramentas ao modelo. O painel é independente dos fluxos sintéticos existentes. A sugestão de encaminhamento fica na conversa de teste.

A cota SQLite reserva antes da chamada. O limite desta aplicação é US$ 0,05 por dia UTC e US$ 0,25 no ensaio inteiro, com 64 tentativas diárias e oito por sessão. Cada tentativa reserva US$ 0,003. A resposta confirmada ajusta a reserva ao uso, com margem para cache. Um resultado incerto continua reservado. O identificador do pedido impede uma segunda chamada após perda da resposta. A estimativa usa preços públicos, não substitui a fatura e não limita outros usos da mesma chave. As conversas locais ficam disponíveis como contexto por até 24 horas e o conteúdo vencido é descartado na próxima pergunta.

A chave, a base e o histórico ficam fora do Git. O browser recebe a resposta e os títulos das fontes selecionadas. A chamada envia somente a pergunta, contexto curto e trechos selecionados ao destino fixo. O navegador não recebe segredos nem arquivo de base. A pilha anterior permanece em rede interna. As ferramentas nativas de Ubuntu, Cockpit e biblioteca padrão Python evitaram instalar toolchain e um servidor web adicional.

Para suspender a função, o operador pode parar o socket e o serviço `atendimento-piloto-ai`, preservando o histórico e os outros serviços. [Modelo e parâmetros oficiais](https://developers.openai.com/api/docs/models/gpt-6-luna).
