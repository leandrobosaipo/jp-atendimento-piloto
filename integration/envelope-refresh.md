# Renovação controlada do envelope somente leitura

Candidato separado para o piloto existente. Não instala código de atendimento,
não avança os recibos de versão e não conecta novo canal. Python reutiliza a
auditoria SQLite e o contrato existentes; uma implementação Rust aqui duplicaria
uma fronteira operacional pequena sem benefício demonstrado.

Base instalada verificada pelo operador: integração
`b9719fa557eab007979685aedaf698c4f60820d5`, painel
`828bead69ff7df1894c58e0fd831f9595a460a94`. O CLI exige os dois commits e compara
os bytes instalados, independentemente do commit desta ferramenta. Use checkout
isolado e limpo no Dell; não faça checkout no diretório da instalação ativa.

O operador prepara, por canal privado, arquivo root 0600 com somente `origin`
igual a `https://sistema.appbarber.com.br`, `cookieHeader`, `source_generation`,
`source_verified_at` e `captured_at` da sessão atual já verificada na Central.
Exige geração inteira estritamente maior que a instalada e timestamps UTC sem
regressão nem futuro superior a 60 segundos. Não rebatizar uma sessão antiga
com geração nova. Nenhum segredo é argumento de processo ou saída do CLI.
Não extrair nem publicar a sessão em logs, Git ou conversa. A exceção é somente
a consulta autenticada já autorizada do piloto: esta ferramenta não faz chamada
ao provedor, não solicita login nem altera a agenda.

`build(old_bytes, candidate_bytes)` constrói o envelope novo atualizando cookie e
os três campos de proveniência, mantendo todos os outros campos existentes,
incluindo serviços, profissionais e políticas. Calcule
os hashes SHA-256 antigo/novo privadamente no Dell. Não copiar o catálogo da
Central nem promover políticas novas durante a renovação.

O comando `python3 integration/refresh_envelope.py --help` lista os argumentos.
Forneça o commit exato desta fonte validada em CI, os dois commits instalados,
machine-id conferido, caminhos privados de candidato/backup e os hashes
esperados. Sem `--apply`, faz somente preflight. O backup deve ser um nome novo
em diretório root 0700. Uma tentativa existente bloqueia o reaproveitamento.

Com `--apply`, o CLI toma o flock exclusivo já usado pela admissão da integração,
confere eventos concluídos e invariantes, arquiva o original em 0600 com fsync,
para somente `atendimento-piloto-integracao.service`, substitui atomicamente
somente o envelope e inicia o mesmo serviço. Confere o hash da credencial
carregada em `/run/credentials/`, o histórico local e a preservação do estado
SQLite, manifesto, runtime, unit e gateway. Painel/AI não são reiniciados.
Drop-ins da integração ou divergência do unit bloqueiam a operação para revisão.

Em falha, confere identidade de arquivo e bytes esperados antes de qualquer
rollback; alteração de terceiros retém a tentativa sem sobrescrever o arquivo.
Sem divergência, para o mesmo serviço, restaura somente os bytes do envelope, inicia e
confere a credencial antiga/histórico/invariantes. Nunca restaura banco. Se a
restauração também falhar, o CLI retorna `held`; preservar backup e investigar
privadamente, sem repetir a aplicação nem inferir sucesso. A aplicação bem
sucedida retorna `local_credential_reloaded`, que comprova somente recarga local.

O operador ainda precisa conferir preço/disponibilidade pelo caminho real
somente leitura já existente e preservar evidência sanitizada do resultado.
Não usar mensagem de cliente nem chamada de escrita para fabricar prova.

Sete testes offline cobrem preservação de catálogo, origem, injeção de cabeçalho,
backup privado, ordem de recarga, rollback, proveniência, preservação de alterações
de terceiros e caminhos inseguros. CI não prova
recarga systemd real, validade da sessão atual ou resultado de consulta oficial.
Nenhuma renovação ou implantação foi realizada por esta entrega.
