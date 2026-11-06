# Conversas fictícias no Cockpit

Abra **Conversas fictícias**, escolha um dos três clientes de teste e escreva uma mensagem. O histórico mostra os IDs da conversa, da entrada e da saída, a fonte consultada e a tabela anexada. A tela **Teste de atendimento** continua disponível para testar o modelo diretamente.

O painel cria a mensagem recebida pela API de cliente da caixa local e envia seu ID para um webhook autenticado do n8n do piloto. O workflow confere o registro de origem no Chatwoot, prepara a resposta e pede ao adaptador que faça uma única entrega. As três etapas têm destinos fixos. Mensagens criadas fora desse caminho não são despachadas por um webhook global do Chatwoot.

A IA usa o gateway e a cota já instalados. O adaptador assina o contexto operacional no servidor. Preços e vagas são apresentados a partir dos campos validados da consulta oficial. As opções de assunto, serviço, profissional e dia evitam ambiguidade. Um pedido sem elementos suficientes ou com divergência pendente é registrado para revisão. Disponibilidade é apenas leitura de um dia dentro da janela permitida. Os clientes do AppBarber não são importados. O conector não tem operação para reservar, alterar ou cancelar visitas.

As variantes aprovadas ficam fora do Git. Cada evento reserva uma variante do grupo correspondente. A saída usa o anexo nativo do Chatwoot. Antes de confirmar, o adaptador relê a mensagem e verifica os bytes do armazenamento local contra o SHA-256 da arte. O browser recebe a prévia pelo canal privado do Cockpit. IDs repetidos são reconciliados no registro SQLite. Uma gravação com resultado desconhecido exige revisão do mesmo ID; nenhuma repetição de envio é automática.

O adaptador escuta em um endereço da bridge interna exclusivamente para o n8n e em um socket Unix do operador para a tela. A pilha não publica as aplicações na LAN. Os testes de CI executam o produto real para mensagem, workflow e anexos, substituindo apenas AppBarber e o modelo por respostas artificiais. As credenciais e quatro cópias de referência — as fontes privadas, o manifesto, a base e os recibos — ficam fora do Git.
