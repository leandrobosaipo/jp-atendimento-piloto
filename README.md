# Piloto de atendimento

Laboratório fictício de atendimento em Linux amd64. O Compose usa Chatwoot CE para a caixa de entrada, Langflow para fluxos e uma instância própria do n8n. Cada aplicação tem seu papel e seu banco em PostgreSQL. Redis atende os processos do Chatwoot. A rede do projeto é interna e as três interfaces são publicadas no loopback do Linux. Os limites somam 4,375 GiB, incluindo 1,5 GiB para Langflow. A primeira medição no piloto encontrou pressão de memória sob o limite anterior de 1,25 GiB.

O bootstrap usa Compose, systemd e a biblioteca padrão do Python instalados no destino. A preferência por Rust foi avaliada para este projeto. Manter os três produtos prontos e usar as ferramentas nativas para configurar e testar a pilha reduz dependências de compilação no servidor.

O Linux escuta em 127.0.0.1 nas portas 13300 (Chatwoot), 17860 (Langflow) e 15678 (n8n) por sockets do systemd. O encaminhador resolve apenas o container da aplicação pelo rótulo do projeto e deixa os privilégios de root antes de aceitar tráfego. A bridge interna do Docker não oferece publicação nativa de portas. [Comportamento documentado pelo mantenedor](https://github.com/moby/moby/discussions/53256).

O operador publica a fonte, verifica a CI no mesmo commit e confirma 6 GiB de RAM, quatro CPUs e pelo menos 11 GiB livres no convidado. No checkout limpo do Git, `python3 install.py --source-commit COMMIT --machine-id ID` executa a prévia. O argumento adicional `--apply` inicializa somente um projeto novo. A senha administrativa de cada serviço e as chaves do piloto ficam em arquivos privados gerados no convidado. Acesso ao soquete Docker equivale a acesso administrativo.

`python3 verify.py` usa contatos, fluxos e processos sintéticos para exercitar as aplicações, testar persistência após reinício apenas dos serviços do piloto e validar cópias PostgreSQL em novos bancos isolados. Uma interrupção exige conferir os recibos em `private/` e o estado pelo rótulo do Compose antes de retomar. Para suspender o piloto durante uma manutenção, `docker compose stop` preserva os volumes.

As fontes são o [Compose da versão do Chatwoot](https://github.com/chatwoot/chatwoot/blob/v4.19.1/docker-compose.production.yaml), a [documentação Docker do Langflow](https://docs.langflow.org/deployment-docker) e a [documentação de Docker do n8n](https://docs.n8n.io/hosting/installation/docker/). Os dados e serviços do piloto têm ciclo de vida próprio. A habilitação de canais e de modelos exige uma etapa posterior.
