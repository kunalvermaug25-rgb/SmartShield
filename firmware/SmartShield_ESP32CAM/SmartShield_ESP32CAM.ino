/*
  SmartShield — ESP32-CAM (AI Thinker) firmware
  ------------------------------------------------
  Board in Arduino IDE : "AI Thinker ESP32-CAM"  (esp32 by Espressif, core 2.x or 3.x)
  Partition scheme     : "Huge APP (3MB No OTA)"

  What it does
   • Camera live stream     ->  http://<IP>:81/stream
   • Single JPEG frame      ->  http://<IP>/capture        (backend sends this to Gemini)
   • Sensor JSON            ->  http://<IP>/status
   • Test buzzer            ->  http://<IP>/buzz?ms=800
   • Set limits from website->  http://<IP>/config?limit=10&empty=20   (saved in flash)
   • HC-SR04 water depth  -> above the limit: buzzer + both LEDs ON (works even without Wi-Fi/website)
   • MH-RD rain sensor (DO) -> rain detected: system goes into ALERT mode automatically
                               (water level checked twice as fast; no buzzer, no message)

  !!! Apne breadboard ke hisaab se neeche PINS aur WIFI change karo !!!
*/
#include "esp_camera.h"
#include <WiFi.h>
#include <Preferences.h>
#include "esp_http_server.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ===================== 1. WIFI =====================
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// ===================== 2. PINS (change to match your wiring) =====================
// Free pins on AI Thinker ESP32-CAM (SD card not used): 2, 4(flash LED), 12, 13, 14, 15, 3(RX)
#define PIN_TRIG     13   // HC-SR04 TRIG
#define PIN_ECHO     15   // HC-SR04 ECHO  (use a 1k/2k voltage divider: ECHO is 5V!)
#define PIN_BUZZER   14   // buzzer module IO
#define PIN_LED1     12   // LED 1 (with 220Ω resistor)
#define PIN_LED2      2   // LED 2 (with 220Ω resistor)
#define PIN_RAIN_DO   3   // MH-RD digital out (DO). GPIO3 = RX, so Serial input is disabled.

#define BUZZER_ACTIVE_LOW false   // set true if your buzzer module beeps on LOW

// ===================== 3. DEFAULT LIMITS (website can change these) =====================
float waterLimitCm = 10.0;   // water above this -> alarm
float emptyCm      = 20.0;   // distance from sensor to the EMPTY box bottom (calibrate!)

// ===================== camera pins (AI Thinker) =====================
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

Preferences prefs;
httpd_handle_t webServer = NULL;
httpd_handle_t streamServer = NULL;

// live state
volatile float distanceCm = -1;
volatile float waterCm = 0;
volatile bool raining = false;
volatile bool flood = false;
volatile bool buzzerOn = false;
volatile unsigned long buzzTestUntil = 0;

// ------------------------------------------------------------------ sensors
float readDistanceOnce() {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(3);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, 30000UL);   // 30 ms timeout ≈ 5 m
  if (us == 0) return -1;
  return us * 0.0343f / 2.0f;
}

float readDistance() {           // median of 3 for stability
  float a = readDistanceOnce(); delay(15);
  float b = readDistanceOnce(); delay(15);
  float c = readDistanceOnce();
  if (a < 0 && b < 0 && c < 0) return -1;
  if (a < 0) a = (b > 0 ? b : c);
  if (b < 0) b = (a > 0 ? a : c);
  if (c < 0) c = (a > 0 ? a : b);
  return max(min(a, b), min(max(a, b), c));
}

void setBuzzer(bool on) {
  buzzerOn = on;
  digitalWrite(PIN_BUZZER, (on ^ BUZZER_ACTIVE_LOW) ? HIGH : LOW);
}

void updateSensors() {
  raining = (digitalRead(PIN_RAIN_DO) == LOW);   // MH-RD DO goes LOW when wet
  float d = readDistance();
  if (d > 0) {
    distanceCm = d;
    float w = emptyCm - d;
    waterCm = w < 0 ? 0 : w;
  }
  // hysteresis: on at limit, off 0.5 cm below it
  if (!flood && waterCm >= waterLimitCm) flood = true;
  else if (flood && waterCm < waterLimitCm - 0.5) flood = false;

  bool testBuzz = millis() < buzzTestUntil;
  setBuzzer(flood || testBuzz);
  digitalWrite(PIN_LED1, flood ? HIGH : LOW);
  digitalWrite(PIN_LED2, flood ? HIGH : LOW);
}

const char* modeName() { return flood ? "FLOOD" : (raining ? "ALERT" : "NORMAL"); }

// ------------------------------------------------------------------ HTTP handlers
static esp_err_t sendJson(httpd_req_t* req, const char* json) {
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_send(req, json, strlen(json));
}

static esp_err_t statusHandler(httpd_req_t* req) {
  char buf[320];
  snprintf(buf, sizeof(buf),
    "{\"distance_cm\":%.1f,\"water_cm\":%.1f,\"rain\":%s,\"buzzer\":%s,\"led1\":%s,\"led2\":%s,"
    "\"mode\":\"%s\",\"limit_cm\":%.1f,\"empty_cm\":%.1f,\"rssi\":%d,\"uptime\":%lu}",
    distanceCm, waterCm, raining ? "true" : "false", buzzerOn ? "true" : "false",
    flood ? "true" : "false", flood ? "true" : "false", modeName(),
    waterLimitCm, emptyCm, WiFi.RSSI(), millis() / 1000);
  return sendJson(req, buf);
}

static esp_err_t captureHandler(httpd_req_t* req) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  esp_err_t res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static bool queryParam(httpd_req_t* req, const char* key, char* out, size_t len) {
  char q[96];
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return false;
  return httpd_query_key_value(q, key, out, len) == ESP_OK;
}

static esp_err_t buzzHandler(httpd_req_t* req) {
  char v[12]; int ms = 800;
  if (queryParam(req, "ms", v, sizeof(v))) ms = constrain(atoi(v), 100, 5000);
  buzzTestUntil = millis() + ms;
  setBuzzer(true);
  return sendJson(req, "{\"ok\":true}");
}

static esp_err_t configHandler(httpd_req_t* req) {
  char v[16];
  if (queryParam(req, "limit", v, sizeof(v))) { float f = atof(v); if (f > 0) { waterLimitCm = f; prefs.putFloat("limit", f); } }
  if (queryParam(req, "empty", v, sizeof(v))) { float f = atof(v); if (f > 1) { emptyCm = f; prefs.putFloat("empty", f); } }
  char buf[96];
  snprintf(buf, sizeof(buf), "{\"ok\":true,\"limit_cm\":%.1f,\"empty_cm\":%.1f}", waterLimitCm, emptyCm);
  return sendJson(req, buf);
}

static esp_err_t indexHandler(httpd_req_t* req) {
  const char* html = "<html><body style='font-family:sans-serif;background:#111;color:#eee'>"
    "<h2>SmartShield ESP32-CAM</h2><p><a style='color:#9ff0ec' href='/status'>/status</a> · "
    "<a style='color:#9ff0ec' href='/capture'>/capture</a></p><img id=s style='max-width:100%'>"
    "<script>s.src=location.origin.replace(/:\\d+$/,'')+':81/stream'</script></body></html>";
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html, strlen(html));
}

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t streamHandler(httpd_req_t* req) {
  char part[64];
  httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  while (true) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { delay(10); continue; }
    size_t hlen = snprintf(part, sizeof(part), STREAM_PART, fb->len);
    esp_err_t res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, part, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;           // browser closed the stream
  }
  return ESP_OK;
}

void startServers() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.max_uri_handlers = 8;
  httpd_uri_t idx  = { "/",        HTTP_GET, indexHandler,   NULL };
  httpd_uri_t st   = { "/status",  HTTP_GET, statusHandler,  NULL };
  httpd_uri_t cap  = { "/capture", HTTP_GET, captureHandler, NULL };
  httpd_uri_t bz   = { "/buzz",    HTTP_GET, buzzHandler,    NULL };
  httpd_uri_t cf   = { "/config",  HTTP_GET, configHandler,  NULL };
  if (httpd_start(&webServer, &cfg) == ESP_OK) {
    httpd_register_uri_handler(webServer, &idx);
    httpd_register_uri_handler(webServer, &st);
    httpd_register_uri_handler(webServer, &cap);
    httpd_register_uri_handler(webServer, &bz);
    httpd_register_uri_handler(webServer, &cf);
  }
  cfg.server_port = 81;
  cfg.ctrl_port += 1;
  httpd_uri_t stream = { "/stream", HTTP_GET, streamHandler, NULL };
  if (httpd_start(&streamServer, &cfg) == ESP_OK) httpd_register_uri_handler(streamServer, &stream);
}

bool startCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  if (psramFound()) {
    c.frame_size = FRAMESIZE_VGA;     // 640x480 — good for Gemini + smooth stream
    c.jpeg_quality = 12;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    c.frame_size = FRAMESIZE_QVGA;
    c.jpeg_quality = 14;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t* s = esp_camera_sensor_get();
  s->set_vflip(s, 0);       // change to 1 if the picture is upside-down
  s->set_hmirror(s, 0);
  return true;
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);   // avoid brown-out resets on weak USB power
  Serial.begin(115200);
  Serial.println("\n\nSmartShield booting...");

  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED1, OUTPUT);
  pinMode(PIN_LED2, OUTPUT);
  setBuzzer(false);

  prefs.begin("smartshield", false);
  waterLimitCm = prefs.getFloat("limit", waterLimitCm);
  emptyCm      = prefs.getFloat("empty", emptyCm);

  if (!startCamera()) Serial.println("Camera init FAILED — check the ribbon cable / board selection");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to Wi-Fi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(400); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) {
    startServers();
    Serial.println("\n================================");
    Serial.print  ("  SmartShield IP : "); Serial.println(WiFi.localIP());
    Serial.println("  Put this IP in the website (Camera page)");
    Serial.println("================================");
  } else {
    Serial.println("\nWi-Fi failed — sensors + alarm still work offline.");
  }
  delay(200);
  pinMode(PIN_RAIN_DO, INPUT);   // after Serial setup (GPIO3 is RX)
}

void loop() {
  static unsigned long last = 0;
  unsigned long interval = raining ? 500 : 1000;   // ALERT mode: check water twice as fast
  if (millis() - last >= interval) {
    last = millis();
    updateSensors();
    Serial.printf("dist=%.1fcm water=%.1fcm rain=%d mode=%s\n", distanceCm, waterCm, raining, modeName());
  }
  if (buzzTestUntil && millis() > buzzTestUntil) { buzzTestUntil = 0; setBuzzer(flood); }
  if (WiFi.status() != WL_CONNECTED) { static unsigned long r = 0; if (millis() - r > 10000) { r = millis(); WiFi.reconnect(); } }
  delay(5);
}
