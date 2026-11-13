# ZumZumBeedo — monitoramento de colmeias

Painel web em Flask para cadastrar colmeias, iniciar ciclos, receber áudios e acompanhar alterações acústicas. O servidor atual é `src/beeguard_web.py`, iniciado por `app.py` ou `run.py`.

## Executar no Windows

Na pasta que contém `run.py`, prepare o ambiente na primeira instalação:

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python.exe -m pip install -r requirements.txt
```

Se o ambiente já funciona neste computador, não é necessário recriá-lo. Ambientes virtuais copiados de outro computador podem precisar ser recriados.

Inicie o painel:

```powershell
.\.venv-web\Scripts\python.exe run.py
```

Abra http://127.0.0.1:5000. O painel e `/monitor` usam a aplicação nova; `/health` verifica o armazenamento. Localmente, o banco SQLite é criado automaticamente em `data/beeguard.sqlite3`. Não é necessário XAMPP, MySQL ou arquivo de configuração para esse painel.

## Coletar e comparar

1. Cadastre uma colmeia com código e nome.
2. Inicie um ciclo e configure a quantidade de áudios de referência, o limite de alteração e quantas alterações consecutivas geram alerta.
3. Use a ESP32 por Wi-Fi com o [guia de instalação autônoma](docs/ESP32_WIFI.md), o microfone do navegador, um WAV ou o coletor USB alternativo mostrado no painel.
4. Acompanhe comparações com a referência e com a coleta anterior, gráficos, áudios originais e alertas. O painel consulta atualizações a cada 3 segundos.
5. Registre inspeções como saudável, doente ou condição não confirmada e exporte o CSV para construir uma base rotulada.
6. Encerre o ciclo para preservar o histórico e iniciar outra referência.

O formato recebido é WAV PCM16 mono, 16 kHz, exatamente 10 segundos. O microfone exige localhost ou HTTPS. As notificações do navegador dependem de permissão e do painel aberto; os alertas ficam armazenados no banco.

Para o ESP32 conectado por USB, instale o [firmware](firmware/BeeGuardAI/BeeGuardAI.ino), feche o Monitor Serial e execute o comando do painel na pasta do projeto. Troque COM6 pela porta real. Para reenviar pendências:

```powershell
.\.venv-web\Scripts\python.exe run.py capture_field --session UUID_DO_CICLO --flush --server http://127.0.0.1:5000
```

A fila do coletor fica em `data/live/pending_field/`. Faça backup dela e do banco local.

## Verificar

```powershell
.\.venv-web\Scripts\python.exe run.py test
node --check web/static/beeguard.js
```

Os testes web exercitam persistência, ciclos independentes, comparação acústica, alertas, exportação e integração HTTP com o coletor. Testes de pesquisa também são incluídos e podem depender das bibliotecas e datasets antigos.

## Preparação para Vercel

`app.py`, `requirements.txt` e `vercel.json` já estão presentes. A aplicação exige `DATABASE_URL` apontando para PostgreSQL persistente quando está na Vercel; SQLite local não é usado nesse ambiente. O banco guarda os WAVs em `bg_samples.audio` e as análises em campos JSON. Os espectrogramas são recriados a partir desses WAVs e acessíveis no histórico.

O firmware **BeeGuardWiFi** envia diretamente por HTTPS à Vercel, sem coletor no computador, e inicia a gravação ao ligar a placa quando há ciclo ativo. Configure o Wi-Fi, o endereço do servidor e o código da colmeia na placa. O [guia ESP32 + Vercel + Neon](docs/ESP32_WIFI.md) explica ligações, configuração, início/parada e reenvio. O firmware **BeeGuardAI** e `capture_field` continuam como alternativa por USB.

## Alcance da análise

O índice acústico detecta mudanças em relação ao som inicial do ciclo. Não representa probabilidade de doença. Inspeções humanas fornecem os rótulos saudável/doente; o painel atual ainda não treina nem executa um classificador validado de saúde. Ruído, posição do microfone e alterações ambientais também podem mudar o som.

## Código de pesquisa anterior

Os servidores `beeguard_jatai`, `beeguard_field` e `beeguard_monitor` e seus guias foram preservados. Usam outro fluxo, com MySQL e dependências em `requirements/`. O [guia histórico do projeto](docs/README_PROJETO.md) e os [guias de referência](docs/referencia/README.md) descrevem essa arquitetura anterior; não são os passos de inicialização do painel atual.
