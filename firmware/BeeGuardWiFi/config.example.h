#pragma once
// Modelo dos valores de exemplo incluidos em config.h.
static const char WIFI_SSID[] = "ESP32-Ecoflutuador";
static const char WIFI_PASSWORD[] = "12345678";
static const char SERVER_URL[] = "https://zum-zum-beedo.vercel.app"; // No trailing slash.
static const char HIVE_ID[] = "BG01"; // Exactly the code registered in the dashboard.

static const int MIC_WS = 25;
static const int MIC_SD = 33;
static const int MIC_SCK = 26;
static const uint32_t CAPTURE_INTERVAL_MS = 1000; // Pause AFTER an upload, not start-to-start.
// FIRST INSTALL ONLY: true to initialize an empty LittleFS partition.
// Set back to false and upload again before field use: never autoformat pending audio.
static const bool FORMAT_LITTLEFS_IF_MOUNT_FAILED = false;
