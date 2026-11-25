> Guia detalhado preservado. Para os passos atuais, consulte o README.md da raiz e docs/README_PROJETO.md. Caminhos citados em tabelas antigas identificam módulos agora em src/ ou telas em web/templates/.

# BeeGuard Jataí — atualização sobre Campo v3

Objetivo: gravar áudio original das caixas, gerar espectrogramas e comparar o histórico com inspeções e contexto. Não existe nesta entrega um modelo validado de saúde de jataí. “Sem preocupação observada” e “Preocupação observada” são avaliações humanas datadas; o programa não as infere de frequência, intensidade ou cor da imagem.

## Instalação Windows

Requer o BeeGuard Campo v3 já instalado, com `src/beeguard_field.py`, `src/capture_field.py`, `src/field_dataset.py`, `src/export_field.py`, requisitos e dependências anteriores. Também usa `test_field.py` nos testes novos. Os arquivos novos são adicionais; não sobrescrevem firmware, credenciais, banco ou dados anteriores.

Extraia este ZIP diretamente dentro de BeeGuard. Pare o servidor e o coletor anteriores com Ctrl+C. No terminal integrado PowerShell dentro de BeeGuard:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/requirements_jatai.txt
.\.venv\Scripts\python.exe run.py beeguard_jatai --init-db
.\.venv\Scripts\python.exe -m unittest test_jatai -v
.\.venv\Scripts\python.exe run.py beeguard_jatai
```

Abra http://127.0.0.1:5000 e use Ctrl+F5. Painel Campo/revisão/comparação permanece em `/campo`; monitor anterior em `/monitor`. Não execute dois servidores na porta 5000 ao mesmo tempo.

## Cadastrar e coletar

1. Clique em “Cadastrar nova coleta”. Para a caixa cedida, use um ID permanente, por exemplo `JATAI_CEDIDA01`, e o apiário `SESI_VOTORANTIM`.
2. Espécie: mantenha “Jataí · identificação pendente” até confirmação da profissional. Quando identificada como Tetragonisca angustula, Tetragonisca fiebrigi ou Apis mellifera, registre quem a identificou. Nome popular sozinho não identifica inequivocamente a espécie. Colmeias antigas/sem cadastro de espécie aparecem como “Não identificada”. As demais caixas do SESI não são cadastradas automaticamente como jataí.
3. Preencha fase da caixa (recém-instalada/estabelecida/desconhecida), dispositivo, ganho real, posição e distância. O ganho original é 1×; o formulário não altera o firmware. Colmeia recém-doada não implica necessariamente recém-transferida: registre o que ocorreu de fato.
4. Combine a instalação com a profissional. Preserve posição/ganho, entrada livre e condições semelhantes de gravação; não abra ou altere a caixa apenas para obter áudio. O INMP441 capta som aéreo, não mede diretamente vibração estrutural.
5. Copie o comando gerado. Em outro terminal:

```powershell
.\.venv\Scripts\python.exe run.py capture_field --session UUID_GERADO_NO_PAINEL --port COM6
```

6. O programa recebe WAVs de 10 s e gera automaticamente PNGs de 0–4000 Hz em segundo plano. Você pode ouvir o WAV e ver/baixar PNGs em 0–2000, 0–4000 ou 0–8000 Hz.
7. Para terminar, pare o coletor com Ctrl+C, aguarde seu encerramento e finalize a sessão pelo painel `/campo`. Para mudar de caixa, ganho, sensor ou posição, crie nova sessão. Identificador da caixa permanece o mesmo.

Para as cerca de oito colmeias da unidade, atribua IDs individuais (SESI01, SESI02 etc.) e confirme suas espécies. Com um dispositivo USB, faça coletas de uma caixa por vez. Comparação simultânea de oito caixas exige dispositivos/canais adicionais; esta atualização não acrescenta Wi-Fi nem autonomia de energia.

## O que é salvo

- WAV original: `data/live/audio/`, preservado pelo fluxo anterior.
- PNG padrão: `data/live/spectrograms/UUID_HASH_4000Hz.png`.
- Faixas adicionais: PNGs próprios, gerados sob demanda.
- MySQL: tabelas anteriores + `beeguard_species_sessions` e `beeguard_inspections`.
- Fila de envio: continua em `data/live/pending_field/UUID`.

PNG é derivado do WAV e não substitui o áudio para treinamento. Salve backup do banco, WAVs e metadados. Os PNGs podem ser regenerados. Cada imagem mantém título com colmeia/horário, eixos, parâmetros e escala fixa. Variação de cor não equivale a alteração de saúde.

Geração automática usa fila em memória limitada; gravação de WAV/MySQL continua confirmada mesmo se a geração de imagem falhar. O servidor informa falhas de PNG no terminal. Após reiniciar, ele tenta gerar PNGs das últimas 100 gravações com sessão; para recuperar TODAS as imagens pendentes, pare o servidor e execute:

```powershell
.\.venv\Scripts\python.exe run.py beeguard_jatai --render-pending
```

Depois reinicie o servidor normalmente. Abrir uma gravação também gera sua imagem se faltar. Integridade do WAV é conferida por SHA256 antes de plotar. Arquivos temporários de PNG são renomeados atomicamente. WAVs antigos sem sessão continuam fora da análise nova; não recebem rótulos ou espécie retroativamente.

## Análise e protocolo de campo

O espectrograma usa áudio mono 16 kHz, STFT com FFT 2048, janela padrão Hann e hop 512; resolução entre bins de 7,8125 Hz. Escala fixa -100 a 0 dB relativos à amplitude digital 1, sem normalizar cada gravação por seu máximo. Não é uma medição calibrada de dB SPL. Pela amostragem atual não é possível analisar frequências acima de 8 kHz. A prévia JSON reduz apenas as colunas de tempo; o PNG usa a matriz completa.

“Pico do áudio” é o pico do espectro médio captado e pode corresponder a ruído externo, voz, vento, eletricidade ou abelhas. Não há uma frequência universal programada como “saudável”. Faixas de energia no endpoint são descritivas, não limiares biológicos. O gráfico de histórico usa as features já armazenadas no pipeline v1; o pico da prévia STFT pode diferir pela resolução e agregação.

Sugestão inicial para discutir com a profissional:

1. Registrar a condição inicial da caixa e se ela foi transportada/transferida.
2. Gravar períodos regulares em diferentes horários, mantendo a instalação fixa. Como piloto operacional, uma semana ajuda a observar variação diária, mas não comprova saúde ou valida o modelo.
3. Fazer observações externas e inspeções apenas na frequência orientada pela profissional. Registrar fatos, recursos/atividade quando observados, método e responsável.
4. Anotar ruído próximo, obra, manejo, chuva/mudança de tempo ou de local, além de temperatura e umidade efetivamente medidas.
5. Comparar horários semelhantes da MESMA caixa e instalação antes de comparar entre caixas. Registrar ausência de informação como “Não avaliada”/“Não medi”.

Histórico mostra medianas por minuto da intensidade ou pico espectral do áudio. Lacunas não são silêncio. Gravações marcadas para revisão técnica são excluídas do gráfico; o contador mostra quantas pedem revisão. Linhas amarelas marcam inspeções no intervalo coberto por áudio. As inspeções do dia aparecem também como lista. Mudanças de ganho/posição/sessão podem explicar variações; a imagem não determina a causa.

## Inspeções e fatores ambientais

Formulário guarda: colmeia, horário da observação, avaliação humana, responsável, papel (equipe/profissional), contexto, ruído percebido, temperatura/umidade medidas e local da medição (ambiente externo/interior), além de notas. Campo “profissional” é declarado pelo usuário, não verificação de credencial. Os dados ambientais são digitados; não há integração nova de sensores ou meteorologia automática.

Inspeções são eventos independentes e não viram automaticamente rótulo “boa/ruim” de cada áudio próximo. Registros são acrescentados, sem edição destrutiva; em caso de erro registre uma nova observação identificando a correção nas notas. Datas são guardadas em UTC e exibidas em São Paulo. Formulários usam offset atual -03:00 e não são destinados a datas antigas com horário de verão.

## Exportar os dados de jataí

Para sessões ainda sem identificação específica confirmada:

```powershell
.\.venv\Scripts\python.exe run.py export_jatai --species jatai_unconfirmed --output data/processed/jatai_v1
```

Se confirmada Tetragonisca angustula:

```powershell
.\.venv\Scripts\python.exe run.py export_jatai --species tetragonisca_angustula --output data/processed/jatai_angustula_v1
```

Outras opções: `tetragonisca_fiebrigi`, `apis_mellifera`, `unknown`. Para uma caixa, acrescente `--hive JATAI_CEDIDA01`. Para copiar os WAVs, acrescente `--copy-audio`; por padrão exporta caminhos relativos, sem duplicar gigabytes. Use outra pasta de saída para nova versão.

Saída: manifesto, features elegíveis para classificação de som, sessões com espécie/instalação, inspeções em CSV separado e relatório. Identificação pertence à sessão e não é reescrita retroativamente quando uma espécie é confirmada mais tarde; revise a proveniência antes de unir dados antigos e novos. Exportações por espécie evitam mistura automática de jataí/Apis. Inspeções exportadas incluem todo o histórico das caixas presentes no dataset; confira seus horários antes de qualquer associação.

O treinador anterior pode futuramente usar as features de uma espécie para a tarefa SOM/INTERFERÊNCIA, após revisão dos áudios e cobertura de colmeias/classes. Ele não treina saúde. Este servidor não carrega modelos anteriores, nem disponibiliza `--model`, para evitar interpretação de modelos de Apis/população como diagnóstico de jataí. A construção de um modelo de condições da colônia exigirá protocolo, rótulos e avaliação próprios, acompanhados pela profissional.

## Compatibilidade e limites

Serviço local em 127.0.0.1, ESP32 por USB, computador ligado e MySQL existente. Não há acesso remoto/autenticação, sensores ambientais novos ou diagnóstico automático. Cerca de 2,5 GB/dia de WAV na cadência nominal, mais PNGs; acompanhe espaço e backups. O .gitignore anterior já exclui data/live/data/processed/models: não envie gravações, credenciais ou exportações ao GitHub.

Testes usam áudio sintético, banco em memória e mocks da transação. Conferir as novas tabelas e o hardware no MySQL/ESP32 do usuário continua necessário. Para voltar ao Campo v3, pare o servidor novo e execute beeguard_field.py; tabelas adicionadas permanecem e não removem dados anteriores.

Referências de contexto:
- https://zenodo.org/records/13898841 — Tetragonisca fiebrigi, não identificação automática da caixa do projeto.
- https://revistas.usp.br/bolzoo/article/view/121697 — fatores climáticos e atividade externa de Tetragonisca angustula.
