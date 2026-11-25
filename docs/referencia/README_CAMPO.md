> Guia detalhado preservado. Para os passos atuais, consulte o README.md da raiz e docs/README_PROJETO.md. Caminhos citados em tabelas antigas identificam módulos agora em src/ ou telas em web/templates/.

# BeeGuard Campo — coleta, revisão, comparação e treinamento

Esta atualização usa o projeto BeeGuard anterior: audio_pipeline.py, acquisition/, beeguard_server.py, beeguard_monitor.py, beeguard_dashboard.html, requirements/requirements_live.txt e config/beeguard_config.json configurado. Não altera firmware, credenciais, WAVs existentes ou o modelo de população. Extraia os arquivos novos diretamente na pasta BeeGuard. Desenvolvido sobre a última versão entregue neste chat; se seu repositório foi modificado, revise a compatibilidade antes de substituir dependências.

## Instalar — terminal integrado PowerShell dentro de BeeGuard

Pare o servidor antigo e os coletores com Ctrl+C.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/requirements_field.txt
.\.venv\Scripts\python.exe run.py beeguard_field --init-db
.\.venv\Scripts\python.exe -m unittest test_field -v
.\.venv\Scripts\python.exe run.py beeguard_field
```

Abra http://127.0.0.1:5000 e pressione Ctrl+F5. O monitor anterior continua em http://127.0.0.1:5000/monitor.

## Sessão e coleta real

1. Faça o primeiro teste escolhendo **Teste de bancada**. Para instalação em abelhas, escolha **Colmeia real**. Bancada é excluída do treinamento.
2. Preencha apiário, identificador global único da colmeia (ex.: APIARIO01_COLMEIA01), dispositivo, modelo do microfone, ganho digital, posição/distância, responsável e notas.
3. O firmware original tem ganho digital 1×. O formulário registra o ganho; não altera a ESP32. Após mudança de ganho, posição ou aparelho, crie nova sessão. O firmware original ainda imprime indicadores heurísticos de saúde; eles não são usados como diagnóstico ou rótulo nesta aplicação.
4. O painel gera o comando. Em outro terminal, use o UUID real mostrado:

```powershell
.\.venv\Scripts\python.exe run.py capture_field --session UUID_DA_SESSAO --port COM6
```

5. Mantenha ESP32 por USB, computador e servidor ligados. Use cabos/instalação que não obstruam o orifício do microfone; planeje a proteção física com o responsável pelas colmeias. Esta entrega não adiciona Wi-Fi, bateria ou armazenamento na ESP32.
6. Pare o coletor com Ctrl+C, aguarde o término do processo e então encerre a sessão no painel. Recomenda-se uma sessão por dia/instalação, com várias condições/horários representados. Sessões não têm encerramento automático por queda de energia; depois de reiniciar, retome a mesma somente se a instalação não mudou.

Fila de campo em data/live/pending_field/UUID. Ela guarda sessão, colmeia, horário e WAV antes do envio. O servidor só confirma o envio após vincular o áudio à sessão. Retries não duplicam registros. Sessão encerrada aceita pendências capturadas inteiramente no seu período, mas não novas capturas. Na primeira execução o coletor exige servidor disponível; em reinícios offline pode usar a sessão confirmada no cache. Depois de encerrada, uma sessão antiga não deve ser reutilizada para capturar.

Para reenviar pendências mesmo após encerrar:

```powershell
.\.venv\Scripts\python.exe run.py capture_field --session UUID_DA_SESSAO --flush
```

Arquivos recusados com HTTP 4xx viram `.failed.json` e mantêm o WAV para revisão; não são apagados. Arquivos sem confirmação permanecem `.pending.json`. Confira no terminal/pasta antes de declarar a coleta concluída. O coletor antigo continua compatível, mas seus áudios não recebem sessão e ficam fora da exportação de campo.

## Revisar e comparar

Escolha a sessão e clique em **Revisar**. Ouça o WAV, veja o espectrograma e anote:

- **Som de abelhas sem interferência perceptível**: somente quando for possível reconhecer esse som.
- **Interferência externa perceptível**: pode estar sobreposta às abelhas. Não significa ausência de abelhas.
- **Incerto**: evidência insuficiente, silêncio ou dúvida. Excluído do treinamento.
- Atividade visual alta/habitual/baixa: só se observada no período do áudio; deixe “Não observada” caso contrário.
- Contexto verificado: obra, tráfego, clima, manejo ou outro. Desconhecido é uma resposta válida.
- Responsável e notas; marque exclusão para problemas identificados manualmente.

Cada revisão cria nova linha de auditoria; exportação usa a última. Uma previsão não preenche o rótulo humano automaticamente. Revisar uma sessão encerrada evita que novas capturas mudem a paginação. O painel mostra 25 gravações por página. Comparação aceita até quatro sessões e mostra cobertura técnica, revisões, RMS e frequência medianos. RMS não é população nem pressão sonora calibrada. Configurações iguais declaradas não garantem posições/equipamentos acusticamente equivalentes. Sessões podem ter durações/horários diferentes.

Espectrograma visual STFT: mono 16 kHz, FFT 1024, hop 256, visualização 0–2000 Hz, escala digital fixa de -100 a 0 dB relativos à amplitude 1; não dB SPL. A prévia usa uma em cada quatro colunas para transmissão. Treinamento inicial utiliza as 40 características acústicas existentes, não pixels do gráfico. O WAV original é preservado para futuros Log-Mel/CNNs.

## Exportar uma versão do dataset

```powershell
.\.venv\Scripts\python.exe run.py export_field --output data/processed/field_v1
```

Saída:

- manifest.csv: todas as gravações com sessão, instalação, rótulo/revisão, hash, caminho e motivo de exclusão.
- training_features.csv: somente gravações elegíveis, com as 40 características.
- dataset_report.json: cobertura, classes, colmeias/sessões e problemas de integridade.

Padrão: caminhos relativos para os WAVs originais, sem duplicar gigabytes. Para uma cópia transportável com os áudios:

```powershell
.\.venv\Scripts\python.exe run.py export_field --output data/processed/field_v1_portable --copy-audio
```

Para uma sessão: acrescente `--session UUID_DA_SESSAO`. Pasta existente não é sobrescrita; use v2, v3 etc. Exportação é uma fotografia das revisões atuais: depois de corrigir rótulos, exporte uma nova versão. Todas as ocorrências de áudio idêntico elegível são excluídas do treinamento para evitar duplicação entre grupos. Metadados permanecem no manifesto. Gravações sem revisão, incertas, excluídas manualmente, com qualidade “review”, features incompatíveis ou WAV faltando/hash divergente são excluídas. Problemas de acesso ao arquivo são reportados, não escondidos.

## Treinar e usar o primeiro modelo

O treinador exige ao menos 20 gravações elegíveis, 3 colmeias e 5 gravações de cada uma das duas classes. Esses mínimos são uma trava técnica para um piloto, não quantidade suficiente para comprovar desempenho. Distribua ambas as classes entre várias colmeias, sessões, horários e equipamentos. Nunca provoque perturbação nas abelhas só para obter um rótulo.

```powershell
.\.venv\Scripts\python.exe run.py train_field --dataset data/processed/field_v1/training_features.csv --output models/field_sound_v1
```

Pipeline StandardScaler + regressão logística balanceada, usando só características acústicas. Metadados de colmeia, ganho, contexto e rótulos visuais não entram como entradas do modelo. Escalador é ajustado dentro de cada fold. Validação deixa uma colmeia inteira de fora; compara F1 macro com a classe majoritária e salva matriz de confusão/predições fora do treino. Uma classe restrita a uma única colmeia impede o treino. Gravações consecutivas continuam correlacionadas; não confunda milhares de janelas com milhares de observações independentes.

Se o relatório não superar o baseline, mantenha apenas coleta/regras e amplie/revise os dados. Se desejar testar o modelo experimental, pare o servidor e execute:

```powershell
.\.venv\Scripts\python.exe run.py beeguard_field --model models/field_sound_v1/sound_model.joblib
```

A previsão aparece ao revisar o áudio. Modelo é opcional e desativado por padrão; não gera diagnóstico de abandono, saúde ou causa ambiental. Resultados ainda precisam de um teste prospectivo em colmeias/datas novas. O modelo final usa todos os dados elegíveis; métricas vêm dos modelos por fold.

## MySQL, armazenamento e Git

São acrescentadas beeguard_sessions, beeguard_recording_sessions e beeguard_reviews; tabelas e dados anteriores permanecem. Há uma sessão aberta por identificador de colmeia. IDs globais únicos evitam colisões entre apiários. Datas no banco são UTC; painel usa São Paulo. Horário de captura é aproximado, marcado no computador ao receber o cabeçalho, não relógio sincronizado na ESP32.

Áudio PCM16 mono 16 kHz consome aproximadamente 320 kB por gravação de 10 segundos, cerca de 2,5 GB/dia na cadência atual com intervalo de 1 s. Sem política automática de retenção: monitore espaço e faça backup do banco E WAVs; backup só do MySQL não contém os áudios. Não apague áudios já referenciados sem um plano de arquivamento.

O .gitignore anterior já exclui data/live, data/processed, models e WAVs. Suba scripts/HTML/docs; mantenha WAVs, exportações, credenciais e modelos fora do Git. Para voltar ao monitor antigo, pare o servidor e execute beeguard_monitor.py. O coletor antigo pode seguir, mas a coleta de campo exige capture_field.py.

## Verificação e limites

Testes locais cobrem HTTP real em localhost, retries/vínculo, validação de sessão, revisões, exportação, integridade, espectrograma e treino em dados sintéticos. MySQL real e hardware ESP32 precisam ser conferidos no seu computador. Serviço vinculado somente a 127.0.0.1; não há autenticação ou acesso remoto nesta versão. Comparações são descritivas. Um aplicativo de diagnóstico ambiental exige validação e dados adicionais, não está entregue como diagnóstico neste pacote.
