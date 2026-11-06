# Piloto de atendimento

Laboratório fictício de atendimento em Linux amd64. O Compose usa Chatwoot CE para a caixa de entrada, Langflow para fluxos e uma instância própria do n8n. Cada aplicação tem seu papel e seu banco em PostgreSQL. Redis atende os processos do Chatwoot. A rede do projeto é interna e as três interfaces são publicadas no loopback do Linux. Os limites somam 4.125 GiB.

O operador publica a fonte, verifica a CI no mesmo commit e confirma 6 GiB de RAM, quatro CPUs e pelo menos 11 GiB livres no convidado. No checkout limpo do Git, `python3 install.py --source-commit COMMIT --machine-id ID` executa a prévia. O argumento adicional `--apply` inicializa somente um projeto novo. A senha administrativa de cada serviço e as chaves do piloto ficam em arquivos privados gerados no convidado. Acesso ao soquete Docker equivale a acesso administrativo.

`python3 verify.py` usa contatos, fluxos e processos sintéticos para exercitar as aplicações, testar persistência após reinício apenas dos serviços do piloto e validar cópias PostgreSQL em novos bancos isolados. Uma interrupção exige conferir os recibos em `private/` e o estado pelo rótulo do Compose antes de retomar. Para suspender o piloto durante uma manutenção, `docker compose stop` preserva os volumes.

As fontes são o [Compose da versão do Chatwoot](https://github.com/chatwoot/chatwoot/blob/v4.19.1/docker-compose.production.yaml), a [documentação Docker do Langflow](https://docs.langflow.org/deployment-docker) e a [documentação de Docker do n8n](https://docs.n8n.io/hosting/installation/docker/). Os dados e serviços do piloto têm ciclo de vida próprio. A habilitação de canais e de modelos exige uma etapa posterior.
