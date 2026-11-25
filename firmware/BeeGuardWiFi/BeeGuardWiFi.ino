// Standalone ESP32 + INMP441: LittleFS pending WAV -> HTTPS -> BeeGuard.
// Board package: esp32 by Espressif 3.3.x. Library: ArduinoJson 7.x.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include <esp_system.h>
#include <time.h>
#include "config.h"
#include "trusted_root_ca.h"

static const uint32_t RATE = 16000;
static const uint32_t SAMPLE_COUNT = RATE * 10;
static const uint32_t WAV_SIZE = 44 + SAMPLE_COUNT * 2;
static const char WAV_PATH[] = "/pending.wav";
static const char META_PATH[] = "/pending.json";
static bool ready = false;
static bool ntpLogged = false;
static uint32_t retryMs = 2000;
static int32_t rawSamples[512];
static int16_t pcmSamples[512];

void retryLater() {
  delay(retryMs);
  retryMs = min(retryMs * 2, (uint32_t)60000);
}

bool connectNetwork() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) delay(250);
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("Sem Wi-Fi; aguardando. Pendência preservada.");
      return false;
    }
    Serial.printf("[WIFI] Connected; IP: %s\n", WiFi.localIP().toString().c_str());
  }
  // Clock is required for HTTPS certificate validation and capture timestamps.
  if (time(nullptr) < 1735689600) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    uint32_t started = millis();
    while (time(nullptr) < 1735689600 && millis() - started < 20000) delay(250);
    if (time(nullptr) < 1735689600) {
      Serial.println("Sem horário NTP; captura suspensa.");
      return false;
    }
  }
  if (!ntpLogged) {
    Serial.println("[NTP] Time synchronized.");
    ntpLogged = true;
  }
  return true;
}

bool beginRequest(HTTPClient &http, WiFiClientSecure &tls, const char *path) {
  tls.setCACert(BEEGUARD_ROOT_CA);
  tls.setHandshakeTimeout(20);
  http.setConnectTimeout(15000);
  http.setTimeout(65000); // Vercel cold start + Neon wake-up.
  // Use the final HTTPS URL without redirects.
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(tls, String(SERVER_URL) + path)) return false;
  http.addHeader("X-Hive-Id", HIVE_ID);
  return true;
}

bool activeCycle(String &cycle) {
  WiFiClientSecure tls;
  HTTPClient http;
  Serial.printf("[API] GET %s/api/device/config (hive %s)\n", SERVER_URL, HIVE_ID);
  if (!beginRequest(http, tls, "/api/device/config")) {
    Serial.println("[API] HTTPS request setup failed.");
    return false;
  }
  int code = http.GET();
  Serial.printf("[API] Config response: HTTP %d\n", code);
  JsonDocument response;
  bool parsed = code == 200 && !deserializeJson(response, http.getString());
  http.end();
  if (!parsed || response["hive_id"].as<String>() != HIVE_ID
      || response["sample_rate"].as<uint32_t>() != RATE
      || response["recording_seconds"].as<uint32_t>() != 10) {
    Serial.printf("Configuração não confirmada (HTTP %d); aguardando.\n", code);
    return false;
  }
  cycle = response["cycle_id"].as<String>();
  if (!response["recording_enabled"].as<bool>() || cycle.length() != 36) {
    cycle = "";
    Serial.println("Sem ciclo ativo: inicie um ciclo no painel para gravar.");
  }
  return true;
}

bool setupMicrophone() {
  i2s_config_t config = {};
  config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 12;
  config.dma_buf_len = 512;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = MIC_SCK;
  pins.ws_io_num = MIC_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD;
  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) return false;
  return i2s_stop(I2S_NUM_0) == ESP_OK;
}

void littleEndian(uint8_t *buffer, size_t offset, uint32_t value, size_t count) {
  for (size_t i = 0; i < count; ++i) buffer[offset + i] = value >> (8 * i);
}

String newUuid() {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  char id[37];
  snprintf(id, sizeof(id), "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
  return String(id);
}

bool recordAudio(const String &cycle) {
  if (LittleFS.exists(META_PATH) || LittleFS.exists(WAV_PATH)) return false;
  if (LittleFS.totalBytes() - LittleFS.usedBytes() < WAV_SIZE + 16384) {
    Serial.println("Sem espaço LittleFS; use partição de pelo menos 1 MB.");
    return false;
  }
  File file = LittleFS.open("/capture.tmp", FILE_WRITE);
  if (!file) return false;
  uint8_t header[44] = {};
  memcpy(header, "RIFF", 4); memcpy(header + 8, "WAVEfmt ", 8); memcpy(header + 36, "data", 4);
  littleEndian(header, 4, WAV_SIZE - 8, 4); littleEndian(header, 16, 16, 4);
  littleEndian(header, 20, 1, 2); littleEndian(header, 22, 1, 2);
  littleEndian(header, 24, RATE, 4); littleEndian(header, 28, RATE * 2, 4);
  littleEndian(header, 32, 2, 2); littleEndian(header, 34, 16, 2);
  littleEndian(header, 40, SAMPLE_COUNT * 2, 4);
  bool ok = file.write(header, sizeof(header)) == sizeof(header);
  if (i2s_start(I2S_NUM_0) != ESP_OK) ok = false;
  // Drain old DMA blocks after a network pause and allow the microphone to settle.
  for (int i = 0; ok && i < 16; ++i) {
    size_t read = 0;
    ok = i2s_read(I2S_NUM_0, rawSamples, sizeof(rawSamples), &read, pdMS_TO_TICKS(2000)) == ESP_OK && read == sizeof(rawSamples);
  }
  time_t captured = time(nullptr);
  struct tm utc;
  gmtime_r(&captured, &utc);
  char capturedAt[25];
  strftime(capturedAt, sizeof(capturedAt), "%Y-%m-%dT%H:%M:%SZ", &utc);
  Serial.println("Gravando 10 segundos...");
  uint32_t remaining = SAMPLE_COUNT;
  while (ok && remaining) {
    size_t count = min(remaining, (uint32_t)512);
    size_t wanted = count * sizeof(int32_t), total = 0;
    while (ok && total < wanted) {
      size_t got = 0;
      ok = i2s_read(I2S_NUM_0, ((uint8_t *)rawSamples) + total, wanted - total, &got, pdMS_TO_TICKS(2000)) == ESP_OK && got > 0;
      total += got;
    }
    for (size_t i = 0; i < count; ++i) pcmSamples[i] = rawSamples[i] >> 16;
    if (ok) ok = file.write((uint8_t *)pcmSamples, count * 2) == count * 2;
    remaining -= count;
    delay(0);
  }
  i2s_stop(I2S_NUM_0);
  file.flush();
  ok = ok && file.size() == WAV_SIZE;
  file.close();
  if (!ok) { LittleFS.remove("/capture.tmp"); return false; }
  JsonDocument meta;
  meta["id"] = newUuid(); meta["cycle_id"] = cycle;
  meta["hive_id"] = HIVE_ID; meta["captured_at"] = capturedAt;
  File marker = LittleFS.open("/metadata.tmp", FILE_WRITE);
  if (!marker) return false;
  size_t written = serializeJson(meta, marker);
  marker.flush(); marker.close();
  if (written != measureJson(meta)) return false;
  // Publish the WAV first. An orphan WAV after power loss blocks capture for review.
  if (!LittleFS.rename("/capture.tmp", WAV_PATH)) return false;
  if (!LittleFS.rename("/metadata.tmp", META_PATH)) return false;
  return true;
}

bool uploadPending() {
  File marker = LittleFS.open(META_PATH, FILE_READ);
  JsonDocument meta;
  bool valid = marker && !deserializeJson(meta, marker);
  marker.close();
  File file = LittleFS.open(WAV_PATH, FILE_READ);
  if (!valid || !file || file.size() != WAV_SIZE || meta["hive_id"].as<String>() != HIVE_ID) {
    Serial.println("Pendência inconsistente; preservada para revisão via USB.");
    file.close(); return false;
  }
  WiFiClientSecure tls;
  HTTPClient http;
  if (!beginRequest(http, tls, "/api/device/audio")) {
    Serial.println("[UPLOAD] HTTPS request setup failed; WAV preserved.");
    file.close(); return false;
  }
  http.addHeader("Content-Type", "audio/wav");
  http.addHeader("X-Recording-Id", meta["id"].as<String>());
  http.addHeader("X-Cycle-Id", meta["cycle_id"].as<String>());
  http.addHeader("X-Captured-At", meta["captured_at"].as<String>());
  Serial.printf("[UPLOAD] POST %s/api/device/audio; WAV size: %u bytes\n", SERVER_URL, (unsigned)file.size());
  int code = http.sendRequest("POST", &file, file.size());
  Serial.printf("[UPLOAD] HTTP %d\n", code);
  JsonDocument response;
  bool confirmed = (code == 200 || code == 201) && !deserializeJson(response, http.getString())
      && response["storage"] == "ok" && response["id"].as<String>() == meta["id"].as<String>()
      && response["cycle_id"].as<String>() == meta["cycle_id"].as<String>();
  http.end(); file.close();
  if (!confirmed) {
    Serial.printf("Envio não confirmado (HTTP %d); WAV e UUID preservados.\n", code);
    return false;
  }
  // Marker first: an interrupted cleanup leaves an orphan WAV, never a duplicate capture.
  bool removed = LittleFS.remove(META_PATH) && LittleFS.remove(WAV_PATH);
  Serial.println(removed ? "Gravação confirmada no banco." : "Falha na limpeza; captura suspensa.");
  return removed;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  if (!String(SERVER_URL).startsWith("https://")) {
    Serial.println("Configure SERVER_URL com HTTPS."); return;
  }
  if (!LittleFS.begin(FORMAT_LITTLEFS_IF_MOUNT_FAILED)) {
    Serial.println("LittleFS indisponível. Consulte a instalação no guia ESP32."); return;
  }
  if (!setupMicrophone()) { Serial.println("Falha no microfone I2S."); return; }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  ready = true;
  Serial.printf("[BOOT] BioBee ESP32 starting; server: %s; hive: %s\n", SERVER_URL, HIVE_ID);
}

void loop() {
  if (!ready) { delay(1000); return; }
  if (!connectNetwork()) { retryLater(); return; }
  if (LittleFS.exists(META_PATH) || LittleFS.exists(WAV_PATH)) {
    if (!uploadPending()) { retryLater(); return; }
    retryMs = 2000;
    delay(CAPTURE_INTERVAL_MS);
    return;
  }
  String cycle;
  if (!activeCycle(cycle)) { retryLater(); return; }
  retryMs = 2000;
  if (cycle.isEmpty()) { delay(10000); return; }
  if (!recordAudio(cycle)) { Serial.println("Captura incompleta; aguardando."); retryLater(); }
}
