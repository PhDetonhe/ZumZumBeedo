> Guia detalhado preservado. Para os passos atuais, consulte o README.md da raiz e docs/README_PROJETO.md. Caminhos citados em tabelas antigas identificam módulos agora em src/ ou telas em web/templates/.

# BeeGuard — microfone → servidor → MySQL

Extraia os arquivos deste pacote diretamente na pasta principal BeeGuard. Reaproveita audio_pipeline.py e acquisition/capture_audio.py já existentes. Firmware e treinamento não são alterados.

## Preparar (terminal integrado, pasta BeeGuard)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/requirements_live.txt
Copy-Item config/beeguard_config.example.json config/beeguard_config.json
notepad config/beeguard_config.json
```

No JSON, informe usuário e senha do seu MySQL. O servidor MySQL precisa estar instalado e ligado no computador; o conector Python não instala o MySQL Server. Não envie o arquivo com a senha para outras pessoas. Se a configuração já existir, preserve-a e apenas edite seus campos.

## Criar o banco e iniciar o servidor

```powershell
.\.venv\Scripts\python.exe run.py beeguard_server --init-db
.\.venv\Scripts\python.exe run.py beeguard_server
```

O primeiro comando cria banco `beeguard` e tabela `beeguard_recordings` sem apagar bancos ou registros existentes. O usuário configurado precisa poder criar o banco/tabela; depois disso, a operação exige leitura e inserção. O servidor usa Waitress, em localhost, porta 5000.

Abra http://127.0.0.1:5000 para acompanhar os registros. Para verificar a conexão ao banco, em outro terminal integrado:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/health
```

Esperado: `status=ok`, `mysql=ok`, `model=not_enabled`.

## Iniciar a captura contínua (segundo terminal)

Conecte o ESP32 por USB e feche o Serial Monitor. Use o firmware atual, que envia frames BGAR PCM16 de 10 s.

```powershell
.\.venv\Scripts\python.exe run.py capture_to_server --hive BG01
```

A porta padrão é a SERIAL_PORT do acquisition/capture_audio.py. Se necessário, consulte as portas e passe explicitamente a correta:

```powershell
.\.venv\Scripts\python.exe -m serial.tools.list_ports
.\.venv\Scripts\python.exe run.py capture_to_server --hive BG01 --port COM13
```

COM13 é apenas um exemplo: use a porta do ESP32 no seu computador. Não rode os dois coletores simultaneamente.

## Testar o fluxo sem ESP32

Com servidor e MySQL ligados, use uma janela já preparada pelo pipeline:

```powershell
$wavTeste = (Get-ChildItem data/processed/urban_v1 -Filter *.wav | Select-Object -First 1).FullName
.\.venv\Scripts\python.exe run.py capture_to_server --send-file "$wavTeste" --hive TESTE
Invoke-RestMethod 'http://127.0.0.1:5000/api/recordings?hive_id=TESTE&limit=1'
```

Esse comando grava um evento da colmeia fictícia TESTE para verificar o fluxo. O horário desse teste é o de envio, não o horário original do UrBAN.

## O que é armazenado

No MySQL: UUID do evento, colmeia, captura UTC aproximada pelo recebimento do cabeçalho USB, recebimento UTC, taxa/duração, SHA-256 e nome do WAV, RMS/clipping/silêncio e 40 descritores acústicos em JSON. Nenhum percentual de saúde, diagnóstico, população ou urgência é inferido nesta versão; `model_status=not_enabled`.

O WAV é preservado no computador em `data/live/audio/`. Os indicadores e o caminho do áudio ficam no MySQL. Não é necessário colocar o WAV como BLOB no banco.

## Funcionamento e interrupções

- Atualização por blocos de 10 s, mais tempo de transmissão/análise; não é streaming sample a sample.
- USB → coletor Python → HTTP local → servidor → MySQL. O ESP32 não precisa de novo Wi-Fi/firmware.
- Captura e envio rodam separadamente. Áudios completos são enfileirados em `data/live/pending/` antes do envio.
- Cada evento recebe um UUID. Retry usa o mesmo UUID, evitando duplicação após resposta perdida.
- Pendências são removidas somente após confirmação do banco; servidor/MySQL fora do ar mantém a fila, que é retomada quando o coletor reinicia.
- Erros HTTP 4xx ficam em arquivos `.failed.json` para revisão; WAVs são preservados.
- WAVs incompletos são descartados. Após erro de porta, o coletor tenta reconectar.
- Para parar, Ctrl+C nos terminais. O servidor não apaga registros anteriores.

Para tentar enviar pendências sem abrir a serial:

```powershell
.\.venv\Scripts\python.exe run.py capture_to_server --flush
```

## Validação

```powershell
.\.venv\Scripts\python.exe -m unittest test_live_pipeline -v
```

Sete testes cobrem HTTP real em localhost, análise, validação de WAV, idempotência, indisponibilidade e preservação da fila. Usam repositório em memória: o teste do MySQL real deve ser feito com os comandos `--init-db`, `/health` e `--send-file` no seu computador. O ambiente de desenvolvimento não dispõe de MySQL Server nem ESP32 conectado.
