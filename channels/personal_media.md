# JPEG pessoal e recibo por parte — candidato offline

`personal_media.py` combina o pin pessoal de `personal.py` com o `Outbox` de
`bridge.py`. Importar o módulo não abre rede. Todas as operações capazes de ler
um arquivo ou chamar o provedor são injetadas pelo executor. A árvore contém
somente seis imagens fictícias nos testes; as artes aprovadas e o manifesto
continuam privados, fora do Git.

O construtor exige o SHA-256 dos **bytes exatos** do manifesto privado, política
`price_source_policy=artwork`, seis entradas `approved=true` (três `servicos` e
três `clube-jp`), IDs e nomes únicos, tamanho e SHA-256 por JPEG. O SHA esperado
deve vir do recibo aprovado independente; calculá-lo apenas do arquivo
apresentado não comprova a aprovação das artes. `prepare`
projeta `clube-jp` para o grupo `clube` do `Outbox`, reserva uma variante dentro
do grupo correto e preserva a escolha ao reprocessar o mesmo evento. Antes de
montar `sendMedia`, o adaptador relê os bytes pelo provedor injetado e confere
tamanho, marcadores JPEG e digest. O transporte usa `message/sendMedia` injetado;
o `Native` de `personal.py` não permite essa rota e não foi ampliado aqui.

`verify_recipient` exige o número pessoal pinado e um JID direto comprovado pela
consulta nativa. Grupo, outro contato, outra instância e eco não passam pelo
escopo. O executor deve chamar `claim_and_advance` com diretório privado 0700:
um arquivo 0600 criado com exclusividade e sincronizado em disco reserva a
posição antes da tentativa. O claim permanece após aceite, entrega, falha ou
resultado incerto. Uma nova tentativa da mesma posição fica bloqueada. O
`Outbox` também passa `sending` para `accepted` somente com ID nativo; `PENDING`
é aceite, não entrega. A próxima parte requer `confirm_part` da anterior.

`confirm_part` exige testemunho autenticado do coletor, mesmo ID nativo,
instância, `instance_id_hash`, posição, direção de saída e status de entrega ou
leitura. A associação PN↔LID deve coincidir nos dois sentidos e com o número
verificado. Uma releitura nativa independente confere texto exato ou metadados e
SHA do JPEG; sem ela, o aceite continua bloqueado. O campo `authenticated` só
pode ser atribuído por um chamador confiável depois de validar o registro do
coletor, nunca copiado do webhook ou de um cliente. O código não lê diretamente
o coletor e não usa recibo do Chatwoot como prova de WhatsApp.

## Cliente nativo e sequência vinculada ao inbound

`PersonalMediaNative` herda a configuração e o opener sem proxy de `Native`,
mantendo origem e instância fixas. A allowlist aceita somente consulta do número
pessoal pinado, `sendText`, `sendMedia` com JPEG do manifesto aprovado e
`findMessages` para IDs nativos já associados à tentativa. Número, JID, forma
exata do corpo, MIME, legenda, tamanho e SHA dos bytes são validados antes da
requisição HTTP. O teste injeta um opener falso; importar ou construir o cliente
não abre conexão. A releitura de mídia exige `media_readback` independente.
Sem bytes completos, a parte JPEG permanece aceita e bloqueada, não entregue.

`PersonalSequence` recebe `opened_at` imutável, o `Outbox` privado e uma função
injetada `read_inbound(record_id)` de fonte confiável. `start` compara o registro
relido com o testemunho apresentado, exige `messages.upsert` direto e
autenticado para o mesmo JID, direção de entrada, ID nativo e horário posterior
à abertura. A tabela SQLite `personal_inbound` guarda o vínculo único entre
evento, registro do coletor e digest do testemunho. O plano de partes fica no
`Outbox`; o claim exclusivo precede cada tentativa. Replay do mesmo inbound
preserva o plano e nunca repete parte aceita, incerta ou já entregue.

`read_inbound` deve verificar de fato a autenticidade e integridade do registro
do coletor antes de devolver o objeto. Um campo `authenticated` no webhook
original não substitui essa releitura. O código não instala esse leitor nem
ativa qualquer rota. A releitura `findMessages` confere identidade e conteúdo;
o status de entrega continua vindo de recibo autenticado separado para cada
posição. `source_main_sha`, identidade de runtime, proteção do remetente único
e autorização da janela são gates do executor, fora deste módulo.

Execute `TMPDIR=/private/tmp python3 channels/test_personal_media.py` no macOS;
execute também `TMPDIR=/private/tmp python3 channels/test_personal_media_native.py`;
em Linux, use um diretório temporário real, privado e sem symlink. Os testes são
ficcionais e não enviam mensagens. Python foi mantido porque o pin, a fila
SQLite e os contratos de recibo do piloto já são Python padrão; introduzir
Rust para essa fronteira estreita aumentaria a transição sem benefício medido.

Pendente antes de qualquer ensaio real: executor isolado e autorizado,
manifesto/bytes privados conferidos no destino, `sendMedia` nativo homologado,
prova de entrega do coletor para a mesma parte e controle de um único remetente.
Este módulo não instala, publica, conecta ou ativa canal.
