#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>       // JSON 파싱 및 생성 라이브러리
#include <base64.h>            // 이미지 바이너리를 문자열로 변환
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// Wi-Fi 세팅
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// 프로토콜 명세에 따른 노드 및 데이터 식별자
#define NODE_ID "esp_01"
String current_data_id = ""; 

WiFiServer tcpServer(8080);

// 세션 복원(Resume)을 위해 프레임 버퍼를 전역으로 유지
camera_fb_t * fb = nullptr; 
const size_t CHUNK_SIZE = 256; // 권장 파라미터 (256B)

// 카메라 핀 설정 (AI-Thinker)
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG; 

  if(psramFound()){
    config.frame_size = FRAMESIZE_VGA; 
    config.jpeg_quality = 10;
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("카메라 초기화 실패!");
    return;
  }

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(500); }
  
  tcpServer.begin();
  Serial.println("\n[ESP32 준비 완료] TCP 포트 8080 대기 중...");
}

// JSON 메시지 전송을 위한 헬퍼 함수
void sendJson(WiFiClient& client, DynamicJsonDocument& doc) {
  String output;
  serializeJson(doc, output);
  client.println(output);
}

void loop() {
  WiFiClient client = tcpServer.available();
  
  if (client) {
    Serial.println("\n[마스터 라즈베리파이 접속]");
    
    while (client.connected()) {
      if (client.available()) {
        String req = client.readStringUntil('\n');
        req.trim();
        if (req.length() == 0) continue;

        // 수신된 JSON 파싱
        DynamicJsonDocument doc(1024);
        DeserializationError error = deserializeJson(doc, req);
        if (error) continue;

        String type = doc["type"].as<String>();

        // ==========================================
        // 6.1 초기 접속 절차 (HELLO 수신)
        // ==========================================
        if (type == "HELLO") {
          Serial.println("[수신] HELLO");
          
          // 1. HELLO_ACK 전송
          DynamicJsonDocument ackDoc(256);
          ackDoc["type"] = "HELLO_ACK";
          ackDoc["node_id"] = NODE_ID;
          sendJson(client, ackDoc);

          // 2. NODE_INFO 전송
          DynamicJsonDocument infoDoc(256);
          infoDoc["type"] = "NODE_INFO";
          infoDoc["node_id"] = NODE_ID;
          infoDoc["status"] = "READY";
          sendJson(client, infoDoc);

          // 3. 사진 캡처 (기존 세션이 없으면 새로 캡처)
          if (fb == nullptr) {
            fb = esp_camera_fb_get();
            current_data_id = "img_" + String(millis()); // 고유 data_id 생성
          }

          int total_chunks = (fb->len + CHUNK_SIZE - 1) / CHUNK_SIZE;

          // 4. DATA_INFO 전송
          DynamicJsonDocument dataInfoDoc(256);
          dataInfoDoc["type"] = "DATA_INFO";
          dataInfoDoc["node_id"] = NODE_ID;
          dataInfoDoc["data_id"] = current_data_id;
          dataInfoDoc["total_chunks"] = total_chunks;
          sendJson(client, dataInfoDoc);
        }
        
        // ==========================================
        // 6.2 & 6.4 전송 시작 및 재접촉 절차
        // ==========================================
        else if (type == "REQUEST_TRANSFER") {
          int start_chunk = doc["start_chunk"];
          Serial.printf("[수신] REQUEST_TRANSFER (시작 청크: %d)\n", start_chunk);
          
          int total_chunks = (fb->len + CHUNK_SIZE - 1) / CHUNK_SIZE;
          int current_chunk = start_chunk;

          while (current_chunk < total_chunks && client.connected()) {
            
            // 1. 청크 데이터 분할 및 Base64 인코딩
            size_t offset = current_chunk * CHUNK_SIZE;
            size_t length = CHUNK_SIZE;
            if (offset + length > fb->len) {
              length = fb->len - offset; // 마지막 청크의 남은 크기
            }
            String encoded_payload = base64::encode(fb->buf + offset, length);

            // 2. CHUNK 전송
            DynamicJsonDocument chunkDoc(2048);
            chunkDoc["type"] = "CHUNK";
            chunkDoc["node_id"] = NODE_ID;
            chunkDoc["data_id"] = current_data_id;
            chunkDoc["chunk_index"] = current_chunk;
            chunkDoc["payload"] = encoded_payload;
            sendJson(client, chunkDoc);

            // 3. 마스터의 ACK 또는 STOP 응답 대기
            bool wait_response = true;
            while (client.connected() && wait_response) {
              if (client.available()) {
                String resp = client.readStringUntil('\n');
                resp.trim();
                DynamicJsonDocument respDoc(512);
                deserializeJson(respDoc, resp);
                
                String respType = respDoc["type"].as<String>();
                
                if (respType == "ACK" && respDoc["chunk_index"] == current_chunk) {
                  current_chunk++; // 다음 청크로 이동
                  wait_response = false;
                } 
                else if (respType == "STOP") {
                  Serial.println("\n[제어] 마스터로부터 STOP 수신. 전송 일시 중단 (PAUSED)");
                  return; // loop()의 처음으로 돌아가 마스터의 다음 접속 대기
                }
              }
            }
          }

          // 모든 청크 전송이 완료된 경우 COMPLETE 처리
          if (current_chunk >= total_chunks) {
            DynamicJsonDocument completeDoc(256);
            completeDoc["type"] = "COMPLETE";
            completeDoc["node_id"] = NODE_ID;
            completeDoc["data_id"] = current_data_id;
            sendJson(client, completeDoc);
            
            Serial.println("\n[전송 완료] 메모리 반환");
            esp_camera_fb_return(fb); // 메모리 해제
            fb = nullptr;             // 포인터 초기화
          }
        }
      }
    }
    client.stop();
    Serial.println("[마스터 연결 종료]");
  }
}