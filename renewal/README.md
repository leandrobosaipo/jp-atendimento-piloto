# Renovação assistida de leitura

O item **Acesso à agenda** no Cockpit inicia uma tentativa quando o operador informa o usuário e a senha do serviço oficial. O navegador roda em um container dedicado. O operador interage com a imagem do desafio na sessão já autenticada do painel. Senhas e códigos de verificação ficam em memória durante a tentativa.

O instalador recebe, em separado, os módulos privados do worker já homologado, referências de compromissos para validar o estabelecimento e a chave pública de custódia. A imagem pública não contém esses dados. O worker confere ao menos dois compromissos distintos usando o validador existente. A sessão candidata é cifrada e retida para promoção posterior pelo atualizador controlado. O serviço não inicia às custas da leitura de status, não executa um relógio e não altera a agenda.

A imagem usa Node e Chromium para executar o adaptador Playwright existente. A interface usa o canal Unix do Cockpit. A CI usa uma rede Docker sem saída, um navegador Chromium real e uma conta simulada. O ensaio cobre o desafio interativo e a proteção de uma candidata. A confirmação de login no serviço oficial depende das credenciais do operador e das regras do estabelecimento.
