# Núcleo operacional do Dell

`package.py` recebe os dez rascunhos privados congelados e referências às credenciais do n8n. Valida a topologia, retira o retry da classificação e preserva sua saída de erro para a revisão humana. Execute sem `--apply` para conferir, ou com `--apply` para gerar dez documentos de rascunho e um manifesto em diretório novo. O empacotamento não ativa workflows. O relógio continua no n8n.

`server.mjs` autentica papel, execução, nó e caminho antes de encaminhar ao handler nativo da Central. Cada tentativa grava um intent exclusivo. Uma retomada exige conferir o recibo e o domínio antes de qualquer outra ação. O pré-sync é vinculado ao mesmo workflow e execução e, no relógio, ao nó ordinário ou forçado correspondente.

`owner.py` inicia em manutenção e usa um lease local com prazo. O dono operacional requer evidência privada do cercamento da origem, drenagem, fila, restauração e dono da entrada. Um arquivo local por si só não verifica o estado do outro host. O ensaio usa somente cópia dos dados, sockets privados e rede interna. No retorno, feche a admissão, reconcilie intents e preserve o histórico. A reabertura do relógio original exige liberação do operador.

O POST em socket de saída exige uma tentativa durável com o mesmo destinatário e conteúdo. O recibo efêmero vincula papel, execução e epoch ao emissor independente. A consulta da identidade Evolution usa apenas a credencial de instância e filtro explícito no servidor. A classificação usa a API OpenAI com limite e ledger compartilhados, texto mínimo, sem ferramentas. O classificador devolve o contrato existente para a revisão do planejador nativo.

O processo Node preserva o handler existente, seus módulos e as rotas do n8n. Os auxiliares Python utilizam SQLite e systemd existentes. Este pacote não concede acesso de escrita à agenda, autorização comercial nem permissão para cadastrar contatos no provedor.
