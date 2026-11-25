# Migration 001: arquivamento de colmeias

O arquivamento usa uma tabela aditiva `bg_hive_archives`. As tabelas de ciclos,
gravações, alertas e inspeções permanecem intactas. A inicialização do backend
cria a tabela com `CREATE TABLE IF NOT EXISTS`; não há alteração nem limpeza de
registros existentes.

SQL equivalente para aplicação manual:

```sql
CREATE TABLE IF NOT EXISTS bg_hive_archives (
    hive_id TEXT PRIMARY KEY REFERENCES bg_hives(id),
    archived_at TEXT NOT NULL
);
```

Para reverter somente a capacidade de arquivamento, a operação reversível é:

```sql
DROP TABLE IF EXISTS bg_hive_archives;
```

Essa reversão torna todas as colmeias visíveis novamente. Ela não remove nem
altera ciclos, gravações, alertas ou inspeções. A migration não foi executada
em banco de produção nesta alteração local.
