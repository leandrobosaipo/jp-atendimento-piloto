# Decisões e homologação do piloto JP

Registro de 07/11/2026. Este documento separa decisões do usuário, evidências
anteriores e testes ainda necessários. É uma atualização documental: não muda
o comportamento instalado nem libera o canal real.

## Decisões confirmadas

- **Respostas da IA:** o usuário aprovou o conteúdo das respostas às perguntas
  prontas sugeridas que testou no painel. A aprovação se limita a essa amostra;
  não inclui formatação ou entrega no WhatsApp, agenda integrada ou envio de imagens.
- **Evolution API:** permanece no Mac Mini Server, onde o usuário informou que
  a sessão já está autenticada. A integração WhatsApp continua nesse local.
  Não migrar, substituir ou recriar a sessão como consequência do piloto Dell.
- **Preços:** em divergência entre AppBarber e a imagem aprovada correspondente,
  vale o preço da imagem aprovada (opção B). A decisão define a regra desejada;
  não altera preços do cadastro e não torna imagens de outro serviço equivalentes.
- **Agenda:** o objetivo aprovado é consultar horários e **agendar, alterar e
  cancelar automaticamente** (opção C). Esse escopo ainda precisa ser implementado
  e testado; não corresponde a uma prova de escrita no AppBarber.

## Clube JP

O objetivo aprovado para a primeira versão é explicar os planos e realizar a
adesão automaticamente (opção B). Ainda faltam implementação, testes e verificação
das condições comerciais aplicáveis, inclusive o que a adesão registra e se
envolve cobrança ou aceite de termos. O objetivo não comprova cadastro concluído
nem autoriza inferir uma cobrança que não foi especificada.

Nenhuma adesão, cobrança ou alteração de assinatura é executada por esta
publicação documental. A automação deve confirmar o resultado efetivo do sistema
antes de anunciar sucesso e reter resultados incertos sem duplicar adesões.

## Encaminhamento humano

Quando o assistente realmente não conseguir decidir ou resolver, deve encaminhar
o caso ao grupo de WhatsApp da JP. O encaminhamento deve informar o canal de
origem do cliente (WhatsApp ou Instagram), nome, resumo da conversa e a situação
ou decisão necessária. Confirmações básicas de agenda não devem gerar
encaminhamento por rotina.

A identidade do grupo destinatário e as condições de segurança e compartilhamento
precisam ser verificadas antes de implementar. Este documento registra os campos
e o destino funcional solicitado, sem identificar clientes, membros ou o grupo
real e sem ativar envio. Não encaminhar histórico integral como substituto do
resumo pedido.

## Experiência solicitada

### Mensagens no WhatsApp

Usar formatação compatível com o WhatsApp e dividir a resposta em vários envios
curtos, em ordem, em vez de um texto longo em um único envio. O tom deve ser
natural e conversacional. Quebras de linha dentro da mesma mensagem não cumprem
sozinhas o requisito de vários envios.

A renderização no terminal/painel não comprova a aparência no WhatsApp.
A homologação deve mostrar o resultado no cliente real, inclusive separação,
ordem e formatação. O controle de repetição precisa abranger cada parte enviada,
sem reenviar uma parte cujo resultado esteja incerto.

### Agenda AppBarber

O atendimento deve usar a disponibilidade real do AppBarber. A implementação
futura precisa contemplar consulta, criação, alteração e cancelamento, com
identificação inequívoca da operação e confirmação do resultado no sistema.
Não anunciar reserva, alteração ou cancelamento apenas porque o modelo respondeu
ou uma requisição foi aceita.

Antes da ativação, testar indisponibilidade, horário ocupado entre consulta e
gravação, pedido ambíguo, duplicata e resultado incerto, além dos fluxos normais.
Definir e validar as condições de confirmação com o cliente para operações que
alteram sua agenda. Esses são critérios técnicos de homologação, não decisões
adicionais já respondidas pelo usuário.

### Imagens de preços e promoção

Manter os seis modelos aprovados: três variantes de imagem de preços/serviços
e três de promoção/pacote de manutenção recorrente. As variantes diferem na
aplicação de cores. Selecionar aleatoriamente dentro do grupo correto e
demonstrar variação entre eventos distintos, em vez de usar sempre a mesma arte.

O teste deve mostrar envio e visualização de imagem no WhatsApp, com a legenda
e o preço coerentes com a arte aprovada correspondente. Reprocessar o mesmo
evento deve preservar a variante já reservada, não sortear e enviar outra.
As artes, seus manifestos e os recibos privados continuam fora do repositório.

## Retenção do histórico

O prazo desejado para guardar o histórico das conversas é **1 ano** (opção B),
sujeito a revisão conforme as necessidades da JP e as regras de proteção de dados.
A retenção ainda não foi implementada nem testada.

Este registro não altera configurações de retenção, não ativa limpeza automática
e não apaga dados. Antes de implementar, definir e validar o marco de contagem,
o alcance sobre conversas, anexos e cópias, e eventuais exceções aplicáveis.

## Estado técnico e alcance das provas existentes

A referência consultada é a revisão
`0197e716571a1bc4d20da2fd03b557aa98a835eb`, com
[CI integral anterior](https://github.com/leandrobosaipo/jp-atendimento-piloto/actions/runs/42255910612).
O painel de teste direto da IA e o painel de conversas fictícias têm escopos
diferentes; o teste de respostas relatado pelo usuário não substitui a
homologação integrada.

O [adaptador de conversas fictícias](../integration/README.md) já documenta
consultas controladas de preço e disponibilidade e anexos nativos no Chatwoot.
Os registros técnicos anteriores incluem conferência de texto e bytes das artes.
Isso não prova que o usuário recebeu ou aprovou esses resultados no WhatsApp.

Na revisão consultada:

- AppBarber é somente leitura; não há operação de reservar, alterar ou cancelar.
- Divergência de preço ainda é retida para revisão, sem cotação/anexo conflitante.
  A decisão B foi tomada, mas sua aplicação no código continua pendente.
- O fluxo faz uma única entrega por evento; múltiplos envios precisam de
  implementação e homologação próprias.
- O piloto permanece com contatos fictícios, sem canal WhatsApp/Evolution real.
  Os testes de CI usam respostas artificiais de AppBarber e do modelo.

## Entrada em produção

A escolha para o lançamento é abrir o atendimento para todos **somente depois
dos testes técnicos e da revisão final** (opção B). Não é uma autorização para
ativar agora nem substitui a definição da release e da janela de ativação.

## Critérios antes da produção

- [x] Registrar a aprovação limitada das respostas testadas.
- [x] Definir que Evolution permanece no Mac Mini.
- [x] Definir a imagem aprovada como fonte em divergência de preço.
- [x] Definir o objetivo de agenda com consulta, criação, alteração e cancelamento.
- [ ] Implementar e testar a regra de preço sem contradição entre texto e imagem.
- [ ] Demonstrar formatação WhatsApp e vários envios curtos ordenados.
- [ ] Demonstrar as três variantes de cada grupo e reconciliação sem duplicata.
- [ ] Implementar e homologar leitura e escrita de agenda no AppBarber.
- [ ] Verificar condições comerciais e implementar/homologar a adesão automática
      ao Clube JP, sem presumir cobrança ou aceite de termos não especificados.
- [ ] Validar o grupo de encaminhamento, o resumo e o critério de exceção,
      sem escalonar confirmações básicas de agenda.
- [ ] Validar identidade dos eventos, recibos, responsabilidade única de envio,
      retomada e tratamento de resultados incertos.
- [ ] Definir, implementar e testar a retenção desejada de 1 ano, sujeita a revisão,
      sem tratar este registro como autorização para apagar dados.
- [ ] Publicar e aprovar os checks do SHA exato da futura implementação.
- [ ] Definir release, janela de ativação e retorno, com autorização específica.

Publicar esta documentação não executa deploy, migração ou corte de produção.
O [contrato de transição da infraestrutura](https://github.com/leandrobosaipo/vps-dell-local/blob/main/migration/README.md)
continua exigindo prova do destino, um único responsável por processar/enviar
e preservação de novos eventos no retorno. Não restaurar estado antigo sobre
atividade nova.
