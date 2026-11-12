# Evidências | 12/11/2026

- [Etapa 2](etapa-02/README.md) reúne as provas de execução paralela, falha controlada e auditoria de credenciais.

- [Grid](airflow-grid.png) e [Graph](airflow-graph.png) mostram a execução manual reexecutada, com as três tarefas em sucesso.
- [Saúde, agenda, execuções e logs](execucao-airflow.txt) registra o estado da stack, ausência de erros de importação, campos do WFS e extratos das tarefas.
- [Execução manual original](execucao-manual.json) e [estados das tarefas](tarefas-primeira-execucao.txt) identificam o run `manual__validacao_fiap`.
- [Arquivos no host](arquivos-host.json) registra os caminhos absolutos originais antes da transferência para `C:\GSBigData\big-data`, códigos HTTP, formatos, contagens, tamanhos, horários e SHA-256 de dados e metadados.
- [Reexecução](reexecucao.txt) registra as tarefas com sucesso após limpar a data lógica original. Os hashes e horários de modificação foram idênticos antes e depois.
- [Testes unitários](testes-unitarios.txt) registra cinco testes aprovados em Python da imagem do scheduler.
- [Falha HTTP 404 controlada](falha-http-404.txt) registra código de saída 1 e ausência de publicação no diretório de teste.

A execução agendada usou a data lógica 11/11 e a manual usou 12/11. Ambas coletaram em 12/11/2026, com 2 registros internos e 903 feições do GeoSampa por execução. Para a próxima etapa ainda é necessária uma coleta em outro dia.

O scheduler começou antes que os arquivos da DAG fossem gravados. O disparo inicial retornou `DagNotFound`. Após recriar somente o scheduler com descoberta a cada 10 segundos, a DAG foi registrada. As duas primeiras tentativas WFS da execução agendada registraram erro TLS. A terceira passou com a inclusão das CAs públicas do certifi, mantendo a validação do certificado e do hostname.



