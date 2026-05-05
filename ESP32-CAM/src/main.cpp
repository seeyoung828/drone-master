#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include <rom/crc.h> // ESP32 내장 하드웨어 가속 CRC32 사용

// ==========================================
// 1. 네트워크 및 노드 설정
// ==========================================
const char* ssid = "Drone_AP";         // 라즈베리파이 핫스팟 이름
const char* password = "raspberry";    // 라즈베리파이 비밀번호

const char* DRONE_IP = "192.168.50.1"; // 마스터 파이썬의 IP
const int DRONE_PORT = 5005;
const int LOCAL_PORT = 8080;

#define NODE_ID "S01"

// ==========================================
// 2. 전역 변수 및 카메라 핀 설정
// ==========================================
WiFiUDP udp;
camera_fb_t * fb = nullptr; 
const size_t CHUNK_SIZE = 256; 

String data_id = ""; 
int total_chunks = 0;
int current_idx = 0;
uint32_t crc32_val = 0;

unsigned long last_beacon_time = 0;
unsigned long beacon_interval = 100; // 파이썬의 0.1초와 동일
bool is_finished = false;

// AI-Thinker 카메라 핀
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

// ==========================================
// 3. 사진 촬영 및 전송 준비 로직
// ==========================================
void prepare_next_image() {
  if (fb != nullptr) {
    esp_camera_fb_return(fb);
    fb = nullptr;
  }
  
  fb = esp_camera_fb_get();
  if(!fb) { 
    Serial.println("카메라 캡처 실패"); 
    delay(1000); 
    return; 
  }
  
  // Data ID 생성 (ESP32는 내부 RTC 시계가 없으므로 millis() 활용)
  data_id = "img_" + String(millis());
  
  total_chunks = (fb->len + CHUNK_SIZE - 1) / CHUNK_SIZE;
  current_idx = 0;
  
  // CRC32 계산 (ESP32 내장 함수 사용)
  crc32_val = crc32_le(0, fb->buf, fb->len);
  
  is_finished = false;
  beacon_interval = 100; // 비콘 주기 초기화

  Serial.printf("\n>>> [Next Image] 준비 완료 (ID: %s, %d chunks, CRC: 0x%08X)\n", 
                data_id.c_str(), total_chunks, crc32_val);
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM; config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM; config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM; config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM; config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM; config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG; 

  if(psramFound()){
    config.frame_size = FRAMESIZE_VGA; 
    config.jpeg_quality = 10; config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 12; config.fb_count = 1;
  }

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("카메라 초기화 실패!"); return;
  }

  WiFi.begin(ssid, password);
  Serial.print("Wi-Fi 연결 중");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  
  udp.begin(LOCAL_PORT);
  Serial.println("\n--- [Node " NODE_ID "] Multi-Image Queue 가동 ---");
  
  prepare_next_image(); // 첫 사진 준비
}

void loop() {
  // 이미지를 다 보냈으면 잠시 대기 후 새 사진 촬영
  if (is_finished) {
    delay(5000); 
    prepare_next_image();
    return;
  }

  // ==========================================
  // 1. BEACON 발송
  // ==========================================
  if (millis() - last_beacon_time > beacon_interval && fb != nullptr) {
    String header = String("BEACON|") + NODE_ID + "|" + data_id + "|" + total_chunks + "|" + current_idx + "|0|";
    udp.beginPacket(DRONE_IP, DRONE_PORT);
    udp.print(header);
    udp.endPacket();
    
    last_beacon_time = millis();
    Serial.println("[전송] BEACON (마스터 응답 대기 중...)");
  }

  // ==========================================
  // 2. 패킷 수신 대기 (GRANT 또는 ERROR)
  // ==========================================
  int packetSize = udp.parsePacket();
  if (packetSize) {
    char incomingBuf[256];
    int len = udp.read(incomingBuf, 255);
    if (len > 0) incomingBuf[len] = 0;
    
    // 파이썬의 msg = data.decode().split('|') 로직 구현
    char* ptr = strtok(incomingBuf, "|");
    String msg_type = ptr ? ptr : "";
    ptr = strtok(NULL, "|"); String target_id = ptr ? ptr : "";

    if (target_id == NODE_ID) {
      if (msg_type == "GRANT") {
        ptr = strtok(NULL, "|"); String req_data_id = ptr ? ptr : "";
        ptr = strtok(NULL, "|"); int target_idx = ptr ? atoi(ptr) : 0;
        ptr = strtok(NULL, "|"); int num_to_send = ptr ? atoi(ptr) : 0;

        if (req_data_id == data_id) {
          if (target_idx >= total_chunks) {
            is_finished = true; // 완료 조건
          } else {
            // Data Chunks 전송
            current_idx = target_idx;
            for (int i = 0; i < num_to_send; i++) {
              if (current_idx >= total_chunks) break;

              size_t offset = current_idx * CHUNK_SIZE;
              size_t length = CHUNK_SIZE;
              if (offset + length > fb->len) length = fb->len - offset;

              int last_flag = (current_idx == total_chunks - 1) ? 1 : 0;
              String header = String("DATA|") + NODE_ID + "|" + data_id + "|" + total_chunks + "|" + current_idx + "|" + last_flag + "|";

              udp.beginPacket(DRONE_IP, DRONE_PORT);
              udp.print(header);
              udp.write(fb->buf + offset, length); // [핵심] 바이너리 데이터 직접 Write
              udp.endPacket();

              current_idx++;
              delay(5); // 버퍼 오버플로우 방지
            }
            beacon_interval = 100; // 성공 시 비콘 주기 복구
          }
        }
      } 
      else if (msg_type == "ERROR") {
        ptr = strtok(NULL, "|"); String dummy1 = ptr ? ptr : "";
        ptr = strtok(NULL, "|"); String dummy2 = ptr ? ptr : "";
        ptr = strtok(NULL, "|"); String dummy3 = ptr ? ptr : "";
        ptr = strtok(NULL, "|"); String error_code = ptr ? ptr : "";

        Serial.printf("[Error Received] Code: %s\n", error_code.c_str());
        
        if (error_code == "CHECKSUM_FAIL" || error_code == "SESSION_MISMATCH") {
          current_idx = 0; // 처음부터 다시 전송
        } else if (error_code == "TIMEOUT") {
          beacon_interval = min(5000UL, beacon_interval * 2); // 지수 백오프
        }
      }
    }
  }

  // ==========================================
  // 3. 사진 전체 전송 완료 (COMPLETE 보고)
  // ==========================================
  if (is_finished) {
    // 파이썬의 struct.pack('>I', crc32_val) 와 동일하게 빅엔디안으로 변환
    uint8_t crc_buf[4];
    crc_buf[0] = (crc32_val >> 24) & 0xFF;
    crc_buf[1] = (crc32_val >> 16) & 0xFF;
    crc_buf[2] = (crc32_val >> 8)  & 0xFF;
    crc_buf[3] = crc32_val         & 0xFF;

    String header = String("COMPLETE|") + NODE_ID + "|" + data_id + "|0|0|0|";
    
    udp.beginPacket(DRONE_IP, DRONE_PORT);
    udp.print(header);
    udp.write(crc_buf, 4); // 4바이트 CRC 바이너리 첨부
    udp.endPacket();

    Serial.printf("--- [SUCCESS] %s 전송 완료 보고 (ID: %s, CRC32: 0x%08X) ---\n", 
                  NODE_ID, data_id.c_str(), crc32_val);
  }
}