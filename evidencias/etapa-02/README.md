# Evidências da etapa 2

Execução real `manual__etapa02_paralelismo`, data lógica 12/11/2026. O LocalExecutor tinha quatro slots globais, limite de 16 tarefas ativas por DAG e 128 slots no pool padrão. As extrações ocuparam um slot cada e reutilizaram os lotes da etapa 1.

| Tarefa | Início UTC | Fim UTC | Estado |
|---|---|---|---|
| extrair_obstaculos | 17:33:08.740329 | 17:33:09.347537 | success |
| extrair_dados_urbanos | 17:33:08.741056 | 17:33:09.865670 | success |
| verificar_arquivos | 17:33:11.638696 | 17:33:12.801693 | success |

A sobreposição foi de **606,481 ms**. A verificação começou **1,773026 s** após o fim da última extração. Seu resultado persistido correspondeu aos arquivos, contagens, metadados e SHA-256 lidos no host.

- [Dependências, timestamps e resultado](paralelismo.txt)
- [Compose, importação e logs das três tarefas](compose-e-tarefas.txt)
- [Grid](airflow-grid.png) e [Graph](airflow-graph.png) da execução real
- [Hashes e timestamps dos lotes preservados](lotes-preservados.txt)
- [Falha controlada](falha-controlada.txt) com a DAG existente em SQLite temporário. A rota inválida retornou HTTP 404 real, a extração urbana reutilizou a cópia válida e a verificação ficou em `upstream_failed`. A leitura de arquivo ausente e a divergência de checksum também foram rejeitadas.
- [Regressão](testes-unitarios.txt) com sete testes aprovados, incluindo os cinco da etapa 1.
- [Auditoria de segredos](auditoria-segredos.txt) e [diagnóstico redigido do Gitleaks](auditoria-diagnostico.txt).

O Gitleaks 8.30.1 sinalizou cinco linhas por `generic-api-key`. Quatro contêm a URL do endpoint sem autenticação e uma contém a entrada sintética usada para testar a rejeição de credenciais. A classificação automatizada confere caminho, regra e SHA-256 da linha completa. Qualquer alteração na linha ou novo alerta exige revisão. O teste também compara, sem exibir os valores, os quatro segredos locais com os arquivos elegíveis para Git e os logs de serviço e de tarefa disponíveis.

Os comandos para reproduzir estão no [README](../../README.md).
