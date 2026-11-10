# Sincronização e renovação controladas

O núcleo trabalha sobre uma cópia privada. A leitura oficial passa por um socket Unix com validação do UID do consumidor, da origem HTTPS, das rotas e dos parâmetros. O n8n usa credenciais próprias e executa os rascunhos com uma permissão temporária explícita.

Quando a manutenção encontra uma sessão recusada, ela registra um pedido nativo de renovação. O nó seguinte grava a intenção correspondente ao ID da execução e ao ID do pedido. O operador pode conferir os dois recibos e rodar o planejamento no host.

```sh
sudo python3 rearm_worker.py --job ID_DO_PEDIDO
```

O plano exige duas recusas oficiais, ausência de interação humana, uma geração anterior com autenticação comprovada e pelo menos 30 minutos desde o envio anterior de credenciais. A execução com `--apply` obtém exclusão mútua, reserva uma única tentativa por geração, arquiva os recibos e prepara o worker HTTP existente. A senha entra pelo painel autenticado de renovação ou pelo canal cifrado já provisionado. O worker valida pelo menos dois compromissos conhecidos antes de produzir o candidato cifrado.

Se a conexão cair depois da reserva, consulte o pedido, o marcador de tentativa e o recibo do provedor. Cada promoção exige a leitura de preços e disponibilidade antes e depois, com backup do envelope anterior. O núcleo conclui a renovação quando recebe a verificação da identidade e comprova a sincronização da agenda na geração nova. A habilitação de um relógio exige a transferência explícita de responsabilidade entre os ambientes.
