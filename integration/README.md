# Conversas fictícias no Cockpit

## Expediente usado na consulta de vagas

Edite [expediente-jp.json](expediente-jp.json) para alterar os dias, os intervalos de início ou os fechamentos excepcionais. O provedor Python e a regra de reserva em JavaScript leem esse arquivo. No Dell, a pasta da integração e a pasta de configuração montada na Central apontam para o mesmo arquivo físico. Ao publicar uma nova versão por substituição de arquivo, refaça esse vínculo e confira o resultado pela consulta de vagas e pela decisão de reserva da Central. A janela de contato do n8n é uma configuração diferente.

O AppBarber mantém a jornada própria de cada profissional em **Cadastros → Profissionais → Horários de trabalho**. As exceções de uma data ficam em **Configurações → Funcionamento**. Ao atualizar o expediente, confira as duas telas, as vagas retornadas e as reservas já existentes. Uma reserva em dia fechado continua visível até haver um novo combinado com o cliente.

Esta candidata usa o valor e os qualificadores da arte aprovada. A consulta de disponibilidade permanece no AppBarber. Preços da tabela, opções de planos e outras respostas com texto definido entram no histórico do gateway com zero tokens. Dúvidas livres usam o modelo dentro da cota existente. O registro de decisões descreve também os objetivos das etapas posteriores. [Decisões e homologação](../docs/2026-11-07-decisoes-e-homologacao.md)

Abra **Conversas fictícias**, escolha um dos três clientes de teste e escreva uma mensagem. O histórico mostra os IDs da conversa, da entrada e da saída, a fonte consultada e a tabela anexada. A tela **Teste de atendimento** continua disponível para testar o modelo diretamente.

O painel cria a mensagem recebida pela API de cliente da caixa local e envia seu ID para um webhook autenticado do n8n do piloto. O workflow confere o registro de origem e delega uma sequência ao adaptador. Cada parte tem posição, conteúdo e recibo próprios. O texto precede a imagem. Um resultado incerto retém a parte, o restante da sequência e o processamento de novas entradas daquela conversa. As entradas seguintes ficam registradas para revisão. O histórico preserva os recibos de mensagens únicas das revisões anteriores.

O adaptador assina o contexto operacional no servidor. Dúvidas com duas descrições possíveis pedem esclarecimento antes da cotação. Valores sem correspondência exigem revisão. Para consultar vagas, escolha o serviço, o profissional e o dia. A consulta lê um dia dentro da janela permitida.

Para atualizar um piloto existente, use `integration/update.py` com o commit anterior, o novo commit, a identidade do convidado e os hashes anterior e novo do manifesto. A prévia é padrão. Com `--apply`, a atualização fecha temporariamente a entrada, verifica os eventos em andamento e preserva cópias consistentes dos dois SQLite, os arquivos anteriores e um recibo em `private/integration-install/update-*`. Um retorno deve manter os eventos, os recibos por parte e o histórico da IA acumulados depois da atualização. Um envio incerto exige reconciliação pelo ID e pela posição. Não restaure bancos antigos sobre esses registros.

As variantes aprovadas ficam fora do Git. Cada evento reserva uma variante do grupo correspondente. A saída usa o anexo nativo do Chatwoot. Antes de confirmar, o adaptador relê a mensagem e verifica os bytes do armazenamento local contra o SHA-256 da arte. O browser recebe a prévia pelo canal privado do Cockpit. IDs repetidos são reconciliados no registro SQLite. Uma gravação com resultado desconhecido exige revisão do mesmo ID; nenhuma repetição de envio é automática.

O adaptador escuta em um endereço da bridge interna exclusivamente para o n8n e em um socket Unix do operador para a tela. A pilha não publica as aplicações na LAN. Os testes de CI executam o produto real para mensagem, workflow e anexos, substituindo apenas AppBarber e o modelo por respostas artificiais. As credenciais e quatro cópias de referência — as fontes privadas, o manifesto, a base e os recibos — ficam fora do Git.
