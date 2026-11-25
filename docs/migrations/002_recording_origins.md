# Migration 002: origem da gravação

A tabela auxiliar `bg_sample_sources` associa gravações novas à origem sem
alterar linhas de `bg_samples` nem reprocessar o histórico. O endpoint manual
registra `manual`; o endpoint de dispositivo registra `esp32`. Registros
anteriores à migration são apresentados como origem não registrada.

O backend cria a tabela com `CREATE TABLE IF NOT EXISTS` durante a inicialização.
SQL equivalente:

```sql
CREATE TABLE IF NOT EXISTS bg_sample_sources (
    recording_id TEXT PRIMARY KEY REFERENCES bg_samples(id),
    source TEXT NOT NULL CHECK(source IN ('manual','esp32'))
);
```

Reversão: `DROP TABLE IF EXISTS bg_sample_sources;`. Ela apaga somente os
rótulos de origem adicionados por esta migration; os WAVs e metadados originais
permanecem em `bg_samples`. A migration não foi executada em produção.
