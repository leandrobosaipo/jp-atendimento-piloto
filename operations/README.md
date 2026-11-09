# Operações de retenção e recuperação: laboratório fictício

Estado: implementação local testada, **não homologada nem ativada em produção**.
Não há cron, timer, webhook, envio de mensagens, exclusão, purge ou quarentena.
O comando `plan` é uma proposta e não uma autorização de expiração. Nenhum
comando altera o banco de origem. A restauração cria outro arquivo e nunca
faz troca de banco, reinício, rollback de serviço ou sobrescrita. Prefira um
diretório novo e privado (0700) para cada ensaio de restauração.

## Executar e testar

Da raiz do checkout:

```sh
python3 -m unittest operations.test_retention -v
python3 operations/retention.py --help
```

Exemplo reproduzível com dados **inteiramente fictícios**:

```sh
LAB=$(mktemp -d)
export LAB
python3 - <<'PY'
import json, os, pathlib, sqlite3
p = pathlib.Path(os.environ['LAB'])
(p / '.fictional-lab').write_text('jp-fictional-lab-v1\n')
(p / 'holds.json').write_text(json.dumps({'version': 1, 'holds': ['held']}))
with sqlite3.connect(p / 'events.sqlite3') as c:
    c.execute('CREATE TABLE events(req TEXT PRIMARY KEY, stamp TEXT, state TEXT)')
    c.executemany('INSERT INTO events VALUES(?,?,?)', [
        ('old', '2024-01-01 00:00:00', 'done'),
        ('held', '2024-01-01 00:00:00', 'done'),
        ('recent', '2026-11-08 00:00:00', 'done')])
PY
python3 operations/retention.py --lab-root "$LAB" plan --source events.sqlite3 --kind integration --anchor 2026-11-09T00:00:00Z --holds holds.json
python3 operations/retention.py --lab-root "$LAB" backup --source events.sqlite3 --destination copy-001
# Somente a próxima chamada escreve um novo bundle fictício:
python3 operations/retention.py --lab-root "$LAB" backup --source events.sqlite3 --destination copy-001 --apply
python3 operations/retention.py --lab-root "$LAB" restore --source copy-001 --destination restored.sqlite3
python3 operations/retention.py --lab-root "$LAB" restore --source copy-001 --destination restored.sqlite3 --apply
python3 operations/retention.py --lab-root "$LAB" alerts --source copy-001 --anchor "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --max-age-hours 24
```

Não use o marcador em diretórios de produção nem copie dados reais para este
laboratório. O marcador é uma declaração explícita do operador, não prova
independente de anonimização. Caminhos externos e symlinks são recusados.
O laboratório deve ser privado e controlado pelo operador: proteção contra um
processo hostil alterando caminhos durante a execução não é o objetivo.

`backup` e `restore` são dry-run por padrão; `--apply` só cria saída nova.
Uma falha preserva artefatos parciais para investigação. Diretório existente,
mesmo incompleto, é recusado; inspecione-o e escolha um destino novo, sem
remover evidências. Manifesto só é gravado após backup e integridade válidos.
Arquivos novos usam 0600 e bundles 0700. O banco copiado inclui todas as tabelas,
índices e dados SQLite, não apenas os eventos elegíveis.

## Política anual proposta

- Marco obrigatório `--anchor`, com timezone explícito e normalizado para UTC.
- Corte calculado pelo aniversário no calendário UTC, não por 365 dias.
  29/02 retrocede para 28/02 quando necessário.
- Somente `stamp < corte` é candidato. Igual ao corte, recente e futuro são
  preservados. Timestamp inválido é preservado.
- Integração: `events.req/stamp/state`, somente estado `done`; timestamps SQL
  sem offset são interpretados como UTC, coerente com `CURRENT_TIMESTAMP`.
- Painel: `turns.id/stamp/status`, epoch em segundos, somente `complete`.
  Estados incertos, pendentes e desconhecidos ficam preservados.
- Inventário de retenções legais é obrigatório, inclusive quando vazio:
  `{"version":1,"holds":["id-do-registro"]}`. Todas as retenções informadas são
  indefinidas. Não existe liberação automática de hold. O inventário é específico
  do banco selecionado; propagação entre sistemas ainda precisa ser definida.
- O marco disponível é a criação original, **não o encerramento ou última
  atividade**. Portanto os candidatos não constituem decisão segura de apagar.
  Partes, recibos, dependências, deduplicação, cotas de consumo e relacionamentos
  devem ser avaliados antes de qualquer política destrutiva.
- O plano não inclui conteúdo de perguntas/respostas; apenas IDs e motivos.
  IDs ainda podem ser sensíveis fora das fixtures e a saída não deve ser publicada.

## Recuperação e alertas: alcance exato

A API SQLite de backup produz snapshot consistente de **um** banco, incluindo
WAL confirmado. `integrity_check`, `foreign_key_check`, tamanho e SHA-256 são
verificados. Somente a conexão do destino novo usa locking_mode=EXCLUSIVE,
evitando um índice WAL persistente em builds SQLite/VFS que deixam -shm.
O modo de bloqueio e o journal da origem são preservados.
O snapshot é finalizado em journal_mode=DELETE e todas as conexões
são fechadas explicitamente antes de publicar o manifesto. Bundles e destinos
com sidecars -wal, -shm ou -journal (inclusive symlinks pendentes) são recusados.
A verificação usa leitura imutável do arquivo autenticado e compara também um
digest lógico do SQL exportado, inclusive após a restauração. Restore verifica
novamente e cria destino exclusivo; o teste
compara eventos e partes restaurados. SHA-256 detecta corrupção, não autentica
um manifesto adulterado junto com o snapshot. Não há assinatura, criptografia,
retenção de cópias, cópia externa, garantia contra perda do disco nem atomicidade
entre o banco de integração e o painel.

Alertas verificam somente integridade e idade de um bundle informado. Retornam
JSON no stdout, código 0 saudável, 2 alerta (ausente, inválido, antigo ou relógio
futuro), 1 erro de uso/execução. O padrão proposto de 24 horas é configurável,
não um SLA aprovado. Nenhum destinatário recebe mensagem e nenhuma rotina é
agendada. Saúde de atendimento, fila, disco, serviços e entrega não é inferida
pela saúde do backup.

## Gates antes de produção ou retenção efetiva

1. Confirmar inventário completo: Chatwoot e demais PostgreSQL, anexos/objetos,
   Redis se necessário, Langflow/n8n, configurações e segredos, Evolution no
   Mac Mini e fontes externas. Estes itens **não estão cobertos** por esta CLI.
2. Definir com o responsável a base legal, marco real (criação/encerramento),
   exceções, holds entre sistemas, cópias e anexos, janela, RPO/RTO, responsáveis
   e destinatários dos alertas. Esta proposta não é parecer jurídico.
3. Implementar backup externo com controles de acesso/criptografia autorizados,
   verificar restauração completa em novos bancos/volumes isolados e registrar
   resultados medidos. Ver `verify.py` para a verificação PostgreSQL existente;
   ela não foi executada nesta entrega e não é coberta pelos testes deste módulo.
4. Publicar fonte e obter CI no SHA exato, homologar atendimento e recuperar
   aplicação completa. Somente então planejar ativação autorizada de rotina.
5. Qualquer remoção permanente exige aprovação específica no momento da ação.
   Não existe caminho de purge nesta implementação, mesmo com `--apply`.
