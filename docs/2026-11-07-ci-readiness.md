# Readiness após reinício no laboratório

## Evidência e limite do diagnóstico

O [run 42374089155](https://github.com/leandrobosaipo/jp-atendimento-piloto/actions/runs/42374089155),
no commit `b6e66a6bf88874e531796505654d28c5ee7e53f6`, iniciou os seis serviços,
mas esgotou a espera HTTP do Chatwoot após o reinício conjunto no teste de
persistência. Os testes posteriores de restauração e integração não foram
concluídos nesse run. O commit anterior passou e a única diferença entre
os dois commits era documental.

Os logs disponíveis não registram o estado dos containers nem o último erro
HTTP. Não permitem comprovar OOM, crash da aplicação ou mudança de IP.

Foi reproduzida uma fragilidade compatível com o sintoma: `units/forward.py`
resolve o IP do container apenas ao iniciar o proxy. Com um destino antigo,
probes a cada três segundos mantêm o proxy ativo, pois cada conexão rearma
o encerramento por dez segundos de inatividade. A reprodução isolada confirma
esse mecanismo, não a causa interna do run citado.

## Mudança proposta

O teste encerra somente as unidades `.service` de encaminhamento após os
reinícios já previstos. As unidades `.socket` continuam em escuta e a próxima
conexão resolve novamente o destino. Isso interrompe conexões desses proxies,
portanto a operação é limitada às interrupções explícitas do teste.

O timeout informa o último tipo de erro ou status HTTP, sem corpo de resposta
ou configuração. Em falhas ordinárias, a CI coleta uma lista limitada de
estado, OOM, exit code, número de reinícios, health direto e via loopback e
estado do proxy. Não publica variáveis de ambiente, logs das aplicações ou
`docker inspect` completo. O diagnóstico recusa redirects HTTP.

Os probes podem ativar proxies em espera no socket. O diagnóstico não altera
configuração nem executa controle de serviço. A coleta por `failure()` não é
garantida se o job for cancelado ou expirar.

Não há mudança de imagens, limites de memória, volumes, regras de retenção ou
conexões externas. Não há renovação automática contínua dos proxies.

## Verificações reproduzíveis

Na raiz do repositório:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile install.py verify.py diagnose_readiness.py
python3 tests/reproduce_proxy_idle.py
```

Os 13 testes unitários passaram no preparo. Docker e controle systemd são
simulados nesses testes; a recusa de redirect usa um servidor HTTP loopback.
A reprodução do proxy usa o executável real do systemd, servidores fictícios
e portas loopback temporárias, sem Docker, privilégios ou serviço instalado.
Ela passou com systemd 257; não substitui a CI integrada no runner Ubuntu.

A aprovação final exige consultar a CI do SHA publicado. Uma CI aprovada
valida o caminho coberto por seus testes, não comprova retroativamente a causa
do run antigo. Este ajuste não autoriza deploy, conexão de canais reais ou
alteração de dados. O acesso e o consumo do servidor instalado exigem
verificação separada; limites configurados não são consumo medido.
