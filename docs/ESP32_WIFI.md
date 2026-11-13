# ESP32 nas colmeias: envio direto para Vercel e Neon

O BeeGuard agora tem um firmware independente do computador: [BeeGuardWiFi](../firmware/BeeGuardWiFi/BeeGuardWiFi.ino). O firmware anterior `BeeGuardAI` permanece disponível para coleta por USB. Os dois são alternativas: grave **BeeGuardWiFi** na placa para usar a rede.

Endereço de referência: **https://zum-zum-beedo.vercel.app**. Em 12/11/2026, a consulta de leitura a `/health` respondeu `status: ok`, `storage: ok`, `backend: postgresql`. Isso confirma a conexão do serviço atual com PostgreSQL; as novas rotas deste guia precisam ser publicadas antes de usar este firmware.

## 1. Como os dados chegam ao banco

```text
INMP441 → I2S → ESP32 → Wi-Fi com internet → HTTPS → Flask na Vercel → PostgreSQL no Neon
```

HTTPS é HTTP com criptografia e validação do servidor. A placa envia um `POST` com um WAV binário de 10 segundos; a aplicação valida, calcula as características, compara com a referência do ciclo e salva tudo em uma transação no PostgreSQL. A ESP32 **não se conecta diretamente ao Neon** e não recebe a senha do banco.

Cada WAV tem **320.044 bytes** (PCM16, mono, 16.000 Hz). Fica abaixo do limite de 1 MiB da aplicação e do [limite de 4,5 MB das Vercel Functions](https://vercel.com/docs/functions/limitations).

## 2. Onde ficam os áudios, as análises e os espectrogramas

| Conteúdo | Armazenamento atual |
| --- | --- |
| Colmeias e ciclos | `bg_hives` e `bg_cycles` no PostgreSQL |
| WAV original | Campo `bg_samples.audio`, tipo `BYTEA` no PostgreSQL |
| RMS, frequência, espectro de 32 faixas e qualidade | `bg_samples.features_json` e `quality_json` |
| Comparações acústicas | `bg_samples.comparison_json` |
| Referência do ciclo | `bg_cycles.baseline_json` |
| Alertas e inspeções humanas | `bg_alerts` e `bg_inspections` |
| Espectrograma de tempo × frequência | Gerado sob demanda a partir do WAV persistido, em `/api/recordings/UUID/spectrogram` |

O gráfico “Assinatura acústica” do painel é um resumo de frequências. O novo link **Espectrograma**, no histórico, mostra a evolução das frequências ao longo dos 10 segundos. Sua imagem SVG contém um mapa de cores PNG e pode ser salva pelo navegador. Não há uma cópia de imagem no Neon: o WAV é a fonte persistente usada para recriá-la, inclusive após reiniciar ou republicar a aplicação.

Ouvir/baixar o WAV: `/api/recordings/UUID/audio`. Localmente, os mesmos dados ficam no SQLite; na Vercel, a aplicação exige `DATABASE_URL` e não usa arquivos locais como armazenamento permanente.

**Capacidade:** cada 1.000 gravações ocupa aproximadamente 320 MB só em WAV, além de índices, metadados e sobrecarga do PostgreSQL. Uma gravação a cada minuto equivale a aproximadamente 461 MB/dia por colmeia; coleta quase contínua pode se aproximar de 2,5 GB/dia. A pausa padrão ocorre depois de gravar e transmitir, portanto a quantidade real depende também da rede. Monitore o armazenamento no Neon e mantenha backup; esta alteração não exclui áudios antigos automaticamente. Para operação prolongada em muitas colmeias, uma evolução indicada é mover os arquivos para armazenamento de objetos, mantendo seus endereços no PostgreSQL.

## 3. Preparar a Vercel

1. Publique o código atualizado no repositório/branch de produção conectado à Vercel. O GitHub publica o servidor, mas **não atualiza o firmware da placa**.
2. Mantenha `DATABASE_URL` com a conexão do Neon, incluindo `sslmode=require`. Para funções com várias conexões curtas, use a conexão com pooling disponibilizada pelo Neon ([documentação](https://neon.com/docs/connect/connection-pooling)). Não coloque essa variável no firmware.
3. Faça um novo deploy. A inicialização das tabelas continua automática; esta alteração não exige apagar nem migrar os áudios existentes.
4. Confira `/health`: deve responder `status: ok`, `storage: ok` e `backend: postgresql`.

A coleta usa apenas o código da colmeia e os identificadores de ciclo e gravação. O envio pela ESP32, pelo navegador e pelo coletor USB funciona sem configurar credenciais de coleta. O código da colmeia na placa precisa ser exatamente o cadastrado no painel.

## 4. Ligar o microfone à ESP32

Este firmware usa a **ESP32 clássica** e o microfone digital **I2S INMP441**, seguindo os pinos do projeto anterior. Outros microfones, placas como C3/S3 ou outra seleção de canal precisam de adaptação e validação antes de instalar.

| INMP441 | ESP32 |
| --- | --- |
| VDD | 3V3 |
| GND | GND |
| SCK / BCLK | GPIO 26 |
| WS / LRCLK | GPIO 25 |
| SD / DOUT | GPIO 33 |
| L/R | GND (canal esquerdo) |

Alimente o INMP441 com 3,3 V. A ESP32 pode receber energia por um carregador USB adequado à placa ou por alimentação regulada apropriada. O computador será usado para configurar e gravar o firmware inicialmente; depois a placa precisa apenas de energia e de uma rede Wi-Fi com internet. A ESP32 clássica usa Wi-Fi de **2,4 GHz**. Sem cobertura no apiário, é necessário disponibilizar conexão, por exemplo com um roteador com acesso à internet.

## 5. Configurar e gravar o firmware

1. Na Arduino IDE, instale **esp32 by Espressif Systems 3.3.x** e **ArduinoJson 7.x**. A compilação foi verificada com core 3.3.12, ArduinoJson 7.4.3 e placa **ESP32 Dev Module**.
2. Na pasta `firmware/BeeGuardWiFi`, abra `config.h`: ele já vem no GitHub com valores de exemplo. Preencha sua cópia antes de gravar a placa:

   ```cpp
   static const char WIFI_SSID[] = "WIFI_DA_COLMEIA";
   static const char WIFI_PASSWORD[] = "SENHA_DO_WIFI";
   static const char SERVER_URL[] = "https://zum-zum-beedo.vercel.app";
   static const char HIVE_ID[] = "BG01";
   ```

3. O certificado público de confiança está em `trusted_root_ca.h`. Ele foi obtido usando uma conexão que valida o certificado e o nome do servidor. Se mudar de domínio ou se a cadeia da hospedagem mudar, atualize-o e grave o firmware novamente:

   ```powershell
   # Este utilitário exige Python 3.13 ou mais recente.
   python src/update_esp32_ca.py --server https://zum-zum-beedo.vercel.app
   ```

   A placa usa `setCACert`, não desativa a validação TLS. Ela sincroniza o relógio por NTP antes da conexão HTTPS; a rede precisa permitir DNS, HTTPS e NTP.

4. Abra `BeeGuardWiFi.ino`. Selecione **ESP32 Dev Module**, a porta USB e uma partição com **pelo menos 1 MB de sistema de arquivos**. O esquema padrão da ESP32 de 4 MB costuma atender; confirme o tamanho no seu modelo. Não selecione um esquema sem SPIFFS/LittleFS.
5. **Somente na primeira instalação**, em uma partição vazia, altere `FORMAT_LITTLEFS_IF_MOUNT_FAILED` para `true`, grave e deixe iniciar para formatar. Antes de levar à colmeia, volte para `false` e grave novamente. Assim, falhas de montagem futuras não apagam gravações pendentes automaticamente. Não ative formatação numa placa que tenha dados a recuperar.
6. Abra o Monitor Serial em **115200 baud** para a verificação inicial. Ele mostra conexão, espera por ciclo, gravação e confirmação. Não há programa coletor a executar no notebook.

## 6. Começar e parar a gravação

1. Abra o painel no computador ou celular e cadastre a colmeia com o mesmo código de `HIVE_ID`.
2. Clique em **Novo ciclo** e configure a referência e os alertas.
3. Ligue a ESP32 conectada ao microfone e à fonte de energia. Ela conecta ao Wi-Fi, sincroniza o horário, consulta o ciclo e grava **automaticamente**. Se já estava ligada sem ciclo, percebe o novo ciclo em uma próxima consulta, normalmente em até 10 segundos, além do tempo de rede.
4. A placa grava um bloco de 10 s, salva na flash, envia por HTTPS e só libera a pendência quando o banco confirma o UUID e o ciclo. O painel pode ficar fechado.
5. Para interromper novas capturas, clique em **Encerrar ciclo**. A placa percebe na próxima consulta. Um bloco que já começou ainda pode ser concluído e enviado para seu ciclo original; não inicia um novo bloco sem ciclo ativo. Para parada física imediata, desligue a alimentação; um bloco ainda em captura pode ficar incompleto.
6. Ao iniciar outro ciclo, a placa consulta o novo identificador automaticamente. Não é necessário alterar `config.h` para cada ciclo.

Existem pausas durante o envio: esta versão não grava um fluxo ininterrupto de áudio. `CAPTURE_INTERVAL_MS` ajusta a pausa **após** o envio, não a duração do WAV. O servidor atual quebra a sequência de alertas quando o intervalo entre os horários de captura supera 60 s; intervalos maiores exigem adaptar essa regra antes de contar alertas consecutivos.

### Falha de internet ou de energia

- Há **uma pendência completa** de cerca de 320 KB em LittleFS. Sem confirmação, a placa tenta reenviar o mesmo WAV, UUID, horário e ciclo com espera progressiva de 2 a 60 segundos. O banco trata a repetição como `already_stored`, sem duplicar.
- Enquanto houver pendência, novas gravações ficam suspensas. Esta versão não mantém uma fila de horas ou dias offline. Sem rede, também não inicia novas capturas, pois precisa confirmar o ciclo ativo.
- Ao religar, uma pendência publicada e íntegra é reenviada antes de consultar um novo ciclo, inclusive se seu ciclo original já terminou. O horário da captura continua sendo o original.
- Queda de energia durante a captura pode perder o bloco incompleto. Uma queda no intervalo entre publicar os arquivos de pendência, ou durante a limpeza, pode deixar arquivos inconsistentes. Nesse caso a placa suspende a captura e pede revisão via USB, preservando os arquivos em vez de sobrescrevê-los. O firmware não fornece uma interface de recuperação automática desses casos.
- Gravações frequentes desgastam a flash interna. Para coleta intensiva permanente ou uma fila offline maior, use uma solução com cartão SD e valide a alimentação e a integridade da captura no hardware.

## 7. Protocolo para verificar a integração

Com o código atualizado publicado, teste a configuração sem criar gravações de teste na base:

```powershell
$headers = @{
  'X-Hive-Id' = 'BG01'
}
Invoke-RestMethod 'https://zum-zum-beedo.vercel.app/api/device/config' -Headers $headers
```

Com ciclo ativo, a resposta contém `recording_enabled: true`, `cycle_id`, `sample_rate: 16000`, `recording_seconds: 10`. Sem ciclo, contém `recording_enabled: false` e `cycle_id: null`.

O envio usa `POST /api/device/audio` com corpo WAV e cabeçalhos:

```http
Content-Type: audio/wav
X-Hive-Id: BG01
X-Cycle-Id: UUID_DO_CICLO_CONSULTADO_ANTES_DA_CAPTURA
X-Recording-Id: UUID_V4_UNICO_POR_GRAVACAO
X-Captured-At: DATA_UTC_DO_INICIO_DA_CAPTURA
```

Confirmação: HTTP `201` para novo áudio ou `200` para repetição, `storage: ok`, `id` e `cycle_id` iguais aos enviados. HTTP `400`: conferir os cabeçalhos e o formato do WAV. `404`: colmeia/ciclo inexistente ou servidor ainda sem atualização. `409`: conflito de UUID/ciclo/horário; a pendência fica preservada para revisão. `503`: armazenamento temporariamente indisponível. Falha TLS: conferir NTP, domínio e certificado de confiança.

## 8. Verificação de software e limites da validação

Os testes de servidor cobrem validação do código da colmeia, consulta de ciclo, envio sem credenciais, gravação/reenvio sem duplicação, reinício, entrega atrasada de áudio de ciclo encerrado e reconstrução do espectrograma a partir do WAV persistido. A compilação verifica o firmware, mas não substitui um teste físico com microfone, fonte e Wi-Fi reais. Não foram enviados áudios sintéticos à base hospedada.

Após gravar a placa, faça a validação de campo: receba e ouça uma gravação, confira o espectrograma, desligue o Wi-Fi durante um envio e verifique que ele aparece uma única vez após reconectar. Confirme também a retomada após desligar/religar com uma pendência completa. Registre ruídos, saturação ou problemas de alimentação antes de considerar o equipamento pronto para uso permanente.

## 9. Preparar no notebook da escola em 13/11/2026

1. Baixe o repositório completo pelo GitHub (**Code → Download ZIP**) e extraia a pasta. Se já houver uma cópia clonada, atualize-a com `git pull`. Mantenha juntos todos os arquivos de `firmware/BeeGuardWiFi`, incluindo `config.h` e `trusted_root_ca.h`.
2. Instale a Arduino IDE, o pacote **esp32 by Espressif Systems 3.3.12** e a biblioteca **ArduinoJson 7.4.3** nesse notebook. As instalações feitas em outro computador não acompanham o ZIP. Para gravar a placa, não é necessário instalar Python nem iniciar o servidor Flask localmente.
3. Conecte a ESP32 com um cabo USB que transfira dados. Confira a porta na Arduino IDE; se ela não aparecer, verifique o cabo e o driver USB correspondente à sua placa.
4. Abra `config.h`, preencha `WIFI_SSID` e `WIFI_PASSWORD`, confira `HIVE_ID` e mantenha `SERVER_URL` como `https://zum-zum-beedo.vercel.app`.
5. Use Wi-Fi **2,4 GHz com internet**, que aceite conexão direta por nome e senha. Este firmware não implementa portal de login em navegador nem autenticação de rede corporativa. Se a rede da escola exigir isso, use uma rede compatível, como um ponto de acesso do celular configurado em 2,4 GHz.
6. Siga a seção 5 para selecionar placa/partição e inicializar LittleFS na primeira instalação. Depois grave com `FORMAT_LITTLEFS_IF_MOUNT_FAILED = false`.
7. No site, confira `/health`, cadastre/selecione a colmeia com o mesmo código da placa e inicie um ciclo. Confira no Monitor Serial em **115200 baud** a mensagem `Gravação confirmada no banco.` e ouça um áudio no histórico.
8. Para testar o servidor atualizado pelo PowerShell, sem iniciar nenhum serviço no notebook:

   ```powershell
   Invoke-RestMethod 'https://zum-zum-beedo.vercel.app/api/device/config' -Headers @{'X-Hive-Id'='BG01'}
   ```

   Com um ciclo ativo da `BG01`, a resposta deve conter `recording_enabled: true` e um `cycle_id`. Se receber `404`, confira se o código atualizado já foi publicado na Vercel e se a colmeia está cadastrada.

Após essa configuração, a ESP32 funciona com alimentação e essa conexão Wi-Fi. Ela consulta o ciclo e envia os áudios diretamente ao servidor hospedado; o notebook pode ser desconectado. O envio por rede e a captura física precisam ser confirmados com o equipamento real antes da apresentação.
