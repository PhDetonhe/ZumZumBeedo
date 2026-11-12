> Guia detalhado preservado. Para os passos atuais, consulte o README.md da raiz e docs/README_PROJETO.md. Caminhos citados em tabelas antigas identificam módulos agora em src/ ou telas em web/templates/.

# BeeGuard — monitoramento acústico de colmeias

Aplicação para aquisição de áudio com ESP32 e microfone INMP441, armazenamento em MySQL, geração de espectrogramas e comparação com observações de campo. O foco atual é acompanhar a caixa de abelhas jataí cedida ao projeto e organizar coletas nas cerca de oito colmeias identificadas na unidade SESI.

**O sistema registra sinais acústicos e apoia a investigação. Não há diagnóstico automático validado de saúde, doença, abandono ou condição ambiental.** Avaliações da colônia devem ser registradas pela equipe e pela profissional que acompanha o projeto.

## 1. Estado atual

| Recurso | Situação |
| --- | --- |
| ESP32 → USB → coletor → servidor → MySQL | Fluxo básico confirmado anteriormente em bancada |
| Sessões por apiário, colmeia e dispositivo | Implementadas no Campo v3 |
| Registro de espécie, identificação e fase da caixa | Implementado na atualização Jataí v4 |
| WAV original e espectrogramas PNG | Implementados, com geração de PNG em segundo plano |
| Histórico acústico e comparação de sessões | Implementados |
| Inspeções, ruído percebido e condições ambientais | Registro manual implementado |
| Exportação rastreável por espécie | Implementada |
| Treinador de som de abelhas/interferência externa | Implementado e testado com dados sintéticos; depende de dados reais revisados |
| Modelo validado de saúde de jataí | Não disponível |

As funcionalidades novas precisam ser verificadas no MySQL e no hardware usados em campo. Testes de software não demonstram desempenho biológico.

## 2. Requisitos e arquivos

- Windows e PowerShell; execute os comandos no terminal integrado do VS Code, dentro da pasta `BeeGuard`.
- Python 3.11 ou 3.12 recomendado para reproduzir o ambiente original. Se a `.venv` atual já funciona, não é necessário recriá-la.
- MySQL Server instalado e em execução. O pacote Python instala o conector, não o servidor MySQL.
- ESP32 com o firmware BeeGuard de aquisição PCM16 e INMP441 conectado conforme `docs/referencia/README_AQUISICAO.md`.
- Código organizado em src/, telas em web/templates/ e ferramentas executadas por run.py.

| Arquivo ou pasta | Responsabilidade |
| --- | --- |
| `firmware/BeeGuardAI/` | Firmware da ESP32 |
| `src/acquisition/` | Aquisição original e instruções de ligação |
| `src/audio_pipeline.py` | Padronização, verificações e features |
| `src/beeguard_server.py` | Recebimento de WAV e persistência; dependência do fluxo atual |
| `src/beeguard_monitor.py` / `web/templates/beeguard_dashboard.html` | Monitor acústico e regras experimentais |
| `src/beeguard_field.py` / `web/templates/beeguard_field.html` | Sessões, revisão de áudio e comparação |
| `src/capture_field.py` | Coletor USB com sessão explícita e fila local |
| `src/beeguard_jatai.py` / `web/templates/beeguard_jatai.html` | Entrada principal atual: espécie, espectrogramas e inspeções |
| `src/export_jatai.py` | Dataset separado por espécie e inspeções em CSV |
| `src/train_field.py` | Treinador experimental de interferência sonora |
| `README_CAMPO.md` / `README_JATAI.md` | Operação detalhada das atualizações |

Os servidores anteriores continuam como dependências e alternativas. **Na operação atual, inicie somente `src/beeguard_jatai.py`; não execute vários servidores na porta 5000.**

## 3. Instalação

Se o ambiente virtual ainda não existir:

```powershell
python -m venv .venv
```

Instale as dependências do pipeline e da atualização atual:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements/requirements_jatai.txt
```

Os requisitos Jataí referenciam `requirements/requirements_field.txt`, que referencia `requirements/requirements_live.txt`. Mantenha esses arquivos em requirements/.

Crie a configuração local apenas se ainda não existir:

```powershell
if (-not (Test-Path config/beeguard_config.json)) {
    Copy-Item config/beeguard_config.example.json config/beeguard_config.json
}
notepad config/beeguard_config.json
```

Preencha host, porta, usuário, senha e banco do MySQL. Não publique `config/beeguard_config.json`. O exemplo contém somente a estrutura; use suas credenciais locais. A inicialização exige permissão para criar o banco/tabelas necessários.

Com o MySQL em execução:

```powershell
.\.venv\Scripts\python.exe run.py beeguard_jatai --init-db
```

Esse comando cria as tabelas ausentes, preservando as existentes.

## 4. Iniciar a aplicação

No primeiro terminal:

```powershell
.\.venv\Scripts\python.exe run.py beeguard_jatai
```

Abra [http://127.0.0.1:5000](http://127.0.0.1:5000) e pressione **Ctrl+F5** após atualizar os arquivos.

| Endereço | Tela |
| --- | --- |
| `/` | Painel Jataí, espectrogramas, histórico e inspeções |
| `/campo` | Revisão dos áudios, encerramento e comparação de sessões |
| `/monitor` | Monitor acústico anterior, com referência e regras experimentais |
| `/health` | Disponibilidade do servidor e conexão ao MySQL |

Para conferir a conexão em outro terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/health
```

O serviço é local, vinculado a `127.0.0.1`. Esta versão não oferece acesso remoto ou autenticação.

## 5. Cadastrar a caixa e coletar áudio

1. Clique em **Cadastrar nova coleta**.
2. Informe apiário e identidade permanente da caixa. Exemplos: `SESI_VOTORANTIM` e `JATAI_CEDIDA01`.
3. Mantenha **Jataí · identificação pendente** até a confirmação da profissional. Para espécie identificada, registre quem confirmou.
4. Informe fase da caixa, dispositivo, microfone, ganho real, posição/distância e responsável.
5. Selecione **Teste de bancada** para ensaios fora da colmeia ou **Colmeia real** para coleta de campo.

O ganho original do firmware é 1×. O formulário registra a configuração; não altera o ganho da ESP32. Se mudar sensor, ganho, posição ou caixa, encerre a sessão e crie outra. O identificador da mesma caixa deve permanecer constante entre sessões.

Conecte o USB **na ESP32**, feche o Monitor Serial do Arduino e confira a porta:

```powershell
.\.venv\Scripts\python.exe -m serial.tools.list_ports
```

A porta anteriormente identificada neste projeto foi **COM6**. No segundo terminal, copie o comando gerado no painel, substituindo o UUID pelo valor real:

```powershell
.\.venv\Scripts\python.exe run.py capture_field --session UUID_DA_SESSAO --port COM6
```

O coletor recebe blocos de 10 segundos, mantém uma fila local e envia o WAV com a identidade da sessão. Ele remove a pendência somente após confirmação do áudio e do vínculo da sessão no MySQL.

Para terminar: **Ctrl+C no coletor**, aguarde o processo encerrar e finalize a sessão em `/campo`. Sessões não são encerradas automaticamente por queda de energia.

Para reenviar pendências de uma sessão encerrada:

```powershell
.\.venv\Scripts\python.exe run.py capture_field --session UUID_DA_SESSAO --flush
```

Pendências recusadas com HTTP 4xx mantêm o WAV e recebem marcador `.failed.json` para revisão. Falhas de conexão preservam `.pending.json`. O horário é marcado pelo computador ao receber o cabeçalho serial; não é um relógio independente sincronizado na ESP32.

**Um dispositivo coleta uma caixa por vez.** Cadastre as outras caixas do SESI individualmente e confirme suas espécies; não assuma que todas são jataí. A coleta atual exige USB e computador ligado.

## 6. Espectrogramas e armazenamento

| Local | Conteúdo |
| --- | --- |
| `data/live/audio/` | WAVs originais |
| `data/live/spectrograms/` | PNGs derivados dos WAVs |
| `data/live/pending_field/UUID/` | Fila local e identificação da sessão |
| `data/processed/` | Exportações versionadas e datasets preparados |
| `data/raw/` | Bases públicas e gravações originais do pipeline offline |
| `models/` | Artefatos de modelos experimentais |

Após uma gravação com sessão, o servidor agenda automaticamente um PNG de **0–4000 Hz**. Na interface, selecione um áudio para ouvir, visualizar ou baixar o espectrograma em **0–2000, 0–4000 ou 0–8000 Hz**.

Parâmetros: PCM16 mono a 16 kHz, STFT com FFT 2048 e hop 512, escala fixa de −100 a 0 dB relativos à amplitude digital 1. **Não são dB SPL calibrados.** Não há normalização de cada gravação pelo próprio pico. O PNG preserva a matriz completa; a prévia JSON reduz colunas de tempo para transmissão.

“Pico do áudio” significa uma característica do sinal captado, que pode incluir abelhas, voz, vento e outros ruídos. Não equivale automaticamente à frequência de voo ou a uma condição da colônia. Não foi programada uma frequência universal de jataí “saudável”.

Se a geração de PNG falhar, o WAV permanece como fonte. O servidor tenta recuperar as últimas 100 gravações após reiniciar. Para gerar todas as imagens pendentes, pare o servidor e execute:

```powershell
.\.venv\Scripts\python.exe run.py beeguard_jatai --render-pending
```

Depois inicie o servidor novamente. Abrir uma gravação também gera a imagem ausente. O SHA-256 do WAV é conferido antes de plotar.

## 7. Inspeções e comparação com fatores ambientais

Registre a observação na sessão da colmeia correta, com:

- Data e horário, responsável e papel declarado: equipe ou profissional.
- Avaliação humana: **Não avaliada**, **Sem preocupação observada** ou **Preocupação observada**.
- Contexto verificado: obra, tráfego, clima, manejo, mudança de local ou outro.
- Ruído externo percebido.
- Temperatura/umidade efetivamente medidas e local da medição: ambiente ou interior.
- Notas sobre o que foi observado e o instrumento/método utilizado.

Esses dados são digitados manualmente; não há novos sensores ambientais ou meteorologia automática. Uma inspeção é um evento datado e **não vira automaticamente um rótulo de todas as gravações próximas**.

O histórico permite selecionar um dia e acompanhar medianas por minuto de RMS ou pico espectral, com marcações de inspeção. Ele reúne as sessões da colmeia; a lista de áudios corresponde à sessão selecionada. Áudios com qualidade técnica para revisão ficam fora do gráfico, e o contador informa sua quantidade. Lacunas não devem ser interpretadas como silêncio ou saúde normal.

Em `/campo`, compare até quatro sessões. Posição, ganho, dispositivo, espécie, horário e duração podem afetar os resultados. Comece comparando a mesma caixa em condições semelhantes. Combine o protocolo de instalação e observação com a profissional, sem alterar ou perturbar a colônia apenas para gerar rótulos.

## 8. Revisar os áudios e exportar dados

Em `/campo`, ouça a gravação e atribua um rótulo humano:

| Rótulo | Significado |
| --- | --- |
| `clean_bee_sound` | Som de abelhas reconhecível, sem interferência perceptível |
| `external_interference` | Interferência externa perceptível, com ou sem abelhas audíveis |
| `uncertain` | Evidência insuficiente; excluído do treinamento padrão |

Atividade visual e contexto são campos separados. Registre atividade somente se observada no período do áudio. Cada revisão acrescenta uma linha de auditoria; a exportação utiliza a mais recente.

Para exportar sessões de jataí ainda sem identificação específica confirmada:

```powershell
.\.venv\Scripts\python.exe run.py export_jatai --species jatai_unconfirmed --output data/processed/jatai_v1
```

Se a espécie foi confirmada como *Tetragonisca angustula*:

```powershell
.\.venv\Scripts\python.exe run.py export_jatai --species tetragonisca_angustula --output data/processed/jatai_angustula_v1
```

Outras opções de espécie: `tetragonisca_fiebrigi`, `apis_mellifera` e `unknown`. Para uma caixa, acrescente `--hive JATAI_CEDIDA01`. Para uma cópia transportável com WAVs, acrescente `--copy-audio`; por padrão são gerados caminhos relativos, sem duplicar gigabytes.

A saída inclui:

- `manifest.csv`: gravações, origem, hash, configuração, revisão e motivos de exclusão.
- `training_features.csv`: exemplos elegíveis e features acústicas.
- `sessions.csv`: identificação da espécie e instalação.
- `inspections.csv`: eventos humanos separados dos rótulos de áudio.
- `dataset_report.json`: cobertura, exclusões e problemas de integridade.

São excluídos do treinamento padrão áudios de bancada, sem revisão, incertos, excluídos manualmente, com qualidade para revisão, features incompatíveis ou WAV faltando/hash divergente. Todas as ocorrências de conteúdo idêntico elegível são removidas do conjunto de treino para evitar duplicação entre grupos; a proveniência permanece no manifesto.

Use nomes novos (`v2`, `v3` etc.) para repetir uma exportação; a pasta existente não é sobrescrita. Identificação da espécie pertence à sessão e não é reescrita retroativamente. Confira a origem antes de reunir sessões anteriores à confirmação da espécie.

## 9. Modelos: o que existe e o que falta

`src/train_field.py` oferece um piloto para **som de abelhas/interferência externa**, com 40 features, padronização e regressão logística balanceada. O treinamento exige pelo menos 20 gravações elegíveis, três colmeias e cinco gravações de cada classe; esses mínimos são uma trava técnica, não comprovação de suficiência dos dados.

Exemplo após exportação e revisão:

```powershell
.\.venv\Scripts\python.exe run.py train_field --dataset data/processed/jatai_v1/training_features.csv --output models/jatai_sound_v1
```

A avaliação deixa uma colmeia inteira fora do treino, ajusta o escalador dentro de cada fold e compara o F1 macro com um baseline de classe majoritária. Metadados de espécie, contexto e inspeção não entram como features do classificador. Gravações próximas continuam correlacionadas; milhares de blocos não equivalem a milhares de observações independentes.

**O servidor Jataí mantém os modelos desativados e não aceita `--model`.** O treinador não estima saúde de jataí. Para o fluxo experimental de inferência do Campo v3, consulte `README_CAMPO.md`; não interprete modelos antigos de *Apis* ou de população como diagnóstico das caixas de jataí.

No piloto de população UrBAN anteriormente executado, o Random Forest teve MAE aproximado de **5,35 quadros**, contra **4,17** do baseline. Ele não superou a referência e foi mantido fora da aplicação operacional.

## 10. Pipeline offline original

O pipeline V1 permanece disponível para preparar WAVs e extrair features, sem depender do servidor.

Para WAVs reais da mesma caixa em `data/raw/beeguard/`:

```powershell
.\.venv\Scripts\python.exe run.py index_recordings --input data/raw/beeguard --output data/metadata/beeguard.csv --source beeguard --hive-id BG01
.\.venv\Scripts\python.exe run.py prepare_dataset --manifest data/metadata/beeguard.csv --output data/processed/beeguard_v1
.\.venv\Scripts\python.exe run.py extract_features --metadata data/processed/beeguard_v1/metadata.csv --output data/processed/beeguard_features.csv
```

Não use um único `hive_id` para arquivos de caixas diferentes. O indexador não adivinha identidade, população ou horário. Campos vazios são desconhecidos.

Resultados: WAVs PCM16 mono 16 kHz de 10 segundos, `metadata.csv`, `report.json` e tabela de features. Por padrão são usadas as primeiras 12 janelas por arquivo; `--max-windows-per-file 0` processa todas. Caudas incompletas são descartadas e registradas. Saídas não são sobrescritas; execução com erro de arquivo ou sem janelas retorna código 1, preservando os resultados válidos.

Verificações técnicas: arquivos corrompidos/não finitos/curtos, duplicação exata por SHA-256, clipping acima de 1% e RMS abaixo de 0,0001. Esses limites iniciais indicam revisão técnica, não qualidade biológica. No pipeline offline, clipping da fonte é medido antes da mistura de canais. Não há normalização de pico ou redução automática de ruído.

Features: MFCC 0–12 com média/desvio, RMS, cruzamentos por zero, frequência dominante, centroide, dispersão, entropia, rolloff, flatness e fração de potência 122–515 Hz. A faixa 122–515 Hz é uma característica descritiva herdada; não é um limiar de saúde validado para jataí. A implementação não reproduz exatamente o benchmark UrBAN.

Contrato do manifesto, com caminhos relativos à pasta do CSV:

| Campo | Significado |
| --- | --- |
| `file` | Caminho do WAV original |
| `source` | Origem explícita, como urban ou beeguard |
| `hive_id` | Identidade persistente da caixa |
| `timestamp` | Início em ISO 8601, com fuso quando conhecido |
| `frames_bees` | Quadros cobertos por abelhas, se realmente conhecido |
| `label_source` | Origem do rótulo e método de associação |
| `inspection_id` | Referência rastreável à inspeção |

O timestamp original é preservado e `start_seconds` representa o deslocamento da janela. Não extrapole inspeções silenciosamente para semanas de áudio. Interpolações são rótulos derivados, não observações diretas.

Os coletores originais em `src/acquisition/` continuam disponíveis, mas não criam sessões de campo. Para a operação atual, prefira `src/capture_field.py`. **“Saúde da colmeia” e “IBA” impressos pelo firmware são heurísticas antigas, não resultados de ML nem indicadores de saúde validados; não são usados como rótulos.**

## 11. Bases públicas e progresso anterior

### UrBAN

- [Dados e DOI](https://doi.org/10.20383/103.0972)
- [Repositório de áudio](https://www.frdr-dfdr.ca/repo/dataset/487f5583-250a-464b-bb71-f2da85410b13)
- [Código e anotações dos autores](https://github.com/mahsa-abdollahi/UrBAN)
- [Artigo](https://doi.org/10.1038/s41597-025-04869-1)

Um subconjunto já foi baixado e processado no desenvolvimento: 382 gravações, dez colmeias e 4.584 janelas. A política experimental de associação de população produziu 348 janelas de 29 gravações e sete colmeias. Esses números descrevem aquele piloto, não dados de jataí nem o conteúdo enviado ao GitHub.

Os CSVs originais `inspections_2021.csv` e `inspections_2022.csv` foram obtidos do repositório dos autores em 05/10/2026. A inspeção inicial encontrou 109 linhas em 2021, 288 em 2022 e 34 eventos explícitos de quadros de abelhas em 2022. Para repetir essa inspeção em outra pasta:

```powershell
.\.venv\Scripts\python.exe run.py inspect_urban --output data/metadata/urban_inspections_v2
```

Os nomes de áudio foram identificados no formato `DD-MM-YYYY_HHhMM_HIVE-tag.wav`. O fuso do áudio permaneceu não confirmado; associações/interpolações do piloto são experimentais, não diagnósticos.

### Outros dados de interesse

- [To bee or not to bee](https://zenodo.org/records/1321278): áudio com intervalos `.lab` para som de abelhas e interferência. `nobee` não significa colmeia vazia. O importador dessa base ainda não está incluído.
- [Áudio e microclima de Tetragonisca fiebrigi](https://zenodo.org/records/13898841): referência de abelha sem ferrão; não deve ser tratada automaticamente como a espécie da caixa cedida.

Preserve espécie, origem, método de coleta e condições de uso de cada base. Não misture dados de espécies diferentes sem uma política documentada e uma avaliação apropriada.

## 12. Testes e solução de problemas

Testes do pipeline original:

```powershell
.\.venv\Scripts\python.exe run.py test
```

Regressão das entregas servidor/monitor/campo/jataí:

```powershell
.\.venv\Scripts\python.exe run.py test
```

Na entrega Jataí v4 foram aprovados 43 testes desses módulos, usando sinais sintéticos, HTTP em localhost, repositório em memória e mocks. Isso não substitui teste com o MySQL real, ESP32 ou abelhas.

| Sintoma | Verificação |
| --- | --- |
| MySQL 2003 / conexão recusada | Serviço MySQL iniciado, host e porta da configuração |
| Porta serial indisponível | Conferir COM, fechar Monitor Serial e outro coletor |
| Interface anterior permanece | Abrir o servidor correto e usar Ctrl+F5 |
| Tabelas de espécie/inspeções ausentes | Executar `beeguard_jatai.py --init-db` com permissão adequada |
| WAV salvo, mas PNG ausente | Conferir terminal, dependências e usar `--render-pending` |
| Exportação vazia ou treino recusado | Conferir espécie, bancada/campo, qualidade, revisões e distribuição de classes/colmeias |
| Dados antigos sem espécie | Permanecem desconhecidos; não são identificados retroativamente |

## 13. Git, armazenamento e próximos passos

Mantenha fora do Git: `.venv/`, `config/beeguard_config.json`, gravações, filas, espectrogramas gerados, datasets processados, modelos e arquivos compactados. Versione código, documentação, requisitos e a configuração de exemplo sem segredos.

A cadência nominal pode produzir cerca de 2,5 GB/dia de WAV, além dos PNGs. Não há retenção automática. Acompanhe espaço e faça backup do MySQL **e dos WAVs**; o banco guarda caminhos/metadados, não o conteúdo de todos os áudios. Os PNGs podem ser regenerados.

Próximos marcos: validar a instalação na caixa cedida, confirmar as espécies, estabelecer protocolo com a profissional, reunir observações confiáveis em diferentes horários/caixas e avaliar modelos em colmeias/datas não usadas no treinamento. Modelos de condição da colônia exigem uma tarefa e critérios próprios; não se resumem a um limiar de frequência.

## ⚠️ Riscos e premissas

- INMP441 mede som aéreo; não é um sensor direto de vibração estrutural.
- Ganho, posição, hardware, horário e ruído externo afetam comparações.
- Temperatura e umidade são registros manuais nesta atualização.
- Ausência de sinal ou de dados pode ser problema de captura; não confirma abandono.
- Uma inspeção pontual não valida todos os áudios próximos.
- Não há score de saúde, diagnóstico de doença ou alerta ambiental validado.
