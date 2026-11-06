# Atendimento sintético

O webhook local autenticado do n8n recebe eventos fictícios em `POST /webhook/pilot-synthetic-v1`. O endereço de origem é o loopback do convidado na porta 15678. O operador lê a credencial `X-Pilot-Fixture` de `private/flows-v1/resources.json` somente no servidor. A configuração do relógio e do workflow usa `America/Cuiaba`.

O contrato aceita `source=fixture`, um `event_id` com prefixo `demo-`, um dos atores de teste e o vocabulário fechado de perguntas descrito em `intake.cjs`. O evento é reservado por chave primária no PostgreSQL antes da primeira ação. Repetir um ID ou alterar seu conteúdo sob o mesmo ID retorna o estado anterior ou conflito. Uma reserva interrompida e uma saída incerta exigem revisão manual.

O Langflow executa Chat Input → Prompt → Chat Output. O prompt formata uma FAQ de teste com uma categoria validada, sem chamar modelo. Uma reclamação, mídia ou identidade ambígua deixa nota privada de revisão na caixa fictícia do Chatwoot. A identidade incerta vai para uma conversa separada. As notas do piloto deixam claro que se trata de registro de laboratório. Somente a FAQ aprovada retorna texto de saída simulada.

O campo opcional `fault` injeta duas situações exclusivas de teste. `tool` chama um fluxo inexistente do Langflow e reencaminha a falha para a equipe na caixa fictícia. `uncertain` conserva a referência da nota interna e retém a saída simulada. Em qualquer repetição, o workflow responde com a retenção registrada.

O operador executa `configure_flows.py` para um ambiente reconciliado com o mesmo Git e recibos. A prévia não grava estado. A execução `--apply` configura somente o n8n e os novos recursos fictícios. `verify_flows.py` testa sete eventos pelas aplicações, abre duas requisições concorrentes, reinicia apenas o relógio do piloto e confere estados no banco e as notas por API do Chatwoot. Os recibos ficam em `private/flows-v1/`. Em falhas, examine a intenção e os IDs antes de retomar. A simulação não comprova atendimento por WhatsApp nem entrega humana.
