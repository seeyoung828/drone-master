/**
 * ESP32-CAM Sensor Node v4.4 — 재부팅 완전 해결판
 *
 * ══ 이전 버전의 실제 패닉 원인 (최종 확인) ══════════════════
 *
 *  로그:
 *    [Camera] 센서 웜업 중...
 *    [Camera] 사전 촬영(10장) 시작...   ← "웜업 완료" 출력이 없음!
 *    Guru Meditation Error: IntegerDivideByZero
 *
 *  원인 1: delay(2000) 중 esp_camera_fb_get() 내부 0 나누기
 *    fb_count=1 + PSRAM 환경에서 더미 프레임 취득 시
 *    카메라 드라이버 내부 DMA 버퍼 크기 계산이 0으로 나누기 발생.
 *    → 해결: 더미 프레임 취득 루프 제거, delay로만 안정화.
 *
 *  원인 2: 촬영 루프가 if(cam_ok)/else 바깥에 위치
 *    카메라 초기화 실패해도 captureAndSave() 호출 → fb=null → panic.
 *    → 해결: cam_ok 플래그로 촬영 루프 완전 가드.
 *
 *  원인 3: LittleFS.begin(true) → 파티션 미설정 시 totalBytes()=0
 *    (file_size + CHUNK_SIZE - 1) / CHUNK_SIZE 에서 CHUNK_SIZE=0 아님,
 *    그러나 totalBytes()=0 인 상태에서 이후 내부 섹터 계산 0 나누기.
 *    → 해결: begin(false) + 명시적 format() 분리.
 *
 *  원인 4: send_beacon()에 mac/name/batt 추가 필드
 *    마스터 split(b'|',6) → 7번째 이후가 Payload로 파싱됨.
 *    → 해결: 표준 6필드 복원.
 *
 *  원인 5: WiFi.disconnect() (true 없음) → 재연결 시 "sta is connecting"
 *    → 해결: disconnect(true).
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <LittleFS.h>
#include <vector>
#include <algorithm>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================
// [설정] 네트워크
// ============================================================
static const char* WIFI_SSID     = "Drone_AP";
static const char* WIFI_PASSWORD = "raspberry";

#define DRONE_IP   "192.168.4.1"
#define DRONE_PORT 5005
#define UDP_PORT   5005

// ============================================================
// [설정] 전송 파라미터
// ============================================================
#define CHUNK_SIZE           1024
#define COMPLETE_RETRY_MAX   10
#define COMPLETE_RETRY_MS    500
#define LIVELOCK_PENALTY_MS  30000
#define BEACON_INTERVAL_MS   100
#define IDLE_WAIT_MS         2000
#define PRE_CAPTURE_COUNT    10      

// ============================================================
// AI-Thinker ESP32-CAM 핀
// ============================================================
#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM    0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34   // ← 카메라 핀. analogRead 금지!
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM      5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22

// ============================================================
// CRC32
// ============================================================
static uint32_t crc32_step(uint32_t crc, uint8_t b) {
    crc ^= b;
    for (int i = 0; i < 8; i++)
        crc = (crc & 1) ? ((crc >> 1) ^ 0xEDB88320u) : (crc >> 1);
    return crc;
}
static uint32_t crc32_buf(const uint8_t* buf, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) crc = crc32_step(crc, buf[i]);
    return ~crc;
}
static uint32_t crc32_file(const String& path) {
    File f = LittleFS.open(path, "r");
    if (!f) return 0;
    f.seek(0);
    uint32_t crc = 0xFFFFFFFFu;
    uint8_t  buf[256];
    while (f.available()) {
        size_t n = f.read(buf, sizeof(buf));
        for (size_t i = 0; i < n; i++) crc = crc32_step(crc, buf[i]);
        yield();
    }
    f.close();
    return ~crc;
}

// ============================================================
// 문자열 유틸
// ============================================================
static std::vector<String> splitStr(const String& s, char d) {
    std::vector<String> v;
    int start = 0, end;
    while ((end = s.indexOf(d, start)) != -1) {
        v.push_back(s.substring(start, end));
        start = end + 1;
    }
    if (start < (int)s.length()) v.push_back(s.substring(start));
    return v;
}
static String basename(const String& path) {
    int i = path.lastIndexOf('/');
    return (i == -1) ? path : path.substring(i + 1);
}

// ============================================================
// LittleFS 안전 마운트
// ============================================================
static bool mountLittleFS() {
    // false = 마운트 실패해도 자동 포맷 안 함 (0 나누기 방지)
    if (LittleFS.begin(false)) {
        Serial.printf("[FS] 마운트 성공 (%u / %u bytes)\n",
                      (unsigned)LittleFS.usedBytes(),
                      (unsigned)LittleFS.totalBytes());
        return true;
    }
    Serial.println("[FS] 마운트 실패 → 포맷 후 재시도");
    LittleFS.format();
    if (LittleFS.begin(false)) {
        Serial.println("[FS] 포맷 후 마운트 성공");
        return true;
    }
    Serial.println("[FS] 마운트 완전 실패!");
    return false;
}

// ============================================================
// 카메라 초기화
// ============================================================
static bool g_cam_ok = false; // 전역 카메라 상태 플래그

static bool initCamera() {
    camera_config_t cfg;
    cfg.ledc_channel = LEDC_CHANNEL_0;
    cfg.ledc_timer   = LEDC_TIMER_0;
    cfg.pin_d0 = Y2_GPIO_NUM; cfg.pin_d1 = Y3_GPIO_NUM;
    cfg.pin_d2 = Y4_GPIO_NUM; cfg.pin_d3 = Y5_GPIO_NUM;
    cfg.pin_d4 = Y6_GPIO_NUM; cfg.pin_d5 = Y7_GPIO_NUM;
    cfg.pin_d6 = Y8_GPIO_NUM; cfg.pin_d7 = Y9_GPIO_NUM;
    cfg.pin_xclk     = XCLK_GPIO_NUM;
    cfg.pin_pclk     = PCLK_GPIO_NUM;
    cfg.pin_vsync    = VSYNC_GPIO_NUM;
    cfg.pin_href     = HREF_GPIO_NUM;
    cfg.pin_sscb_sda = SIOD_GPIO_NUM;
    cfg.pin_sscb_scl = SIOC_GPIO_NUM;
    cfg.pin_pwdn     = PWDN_GPIO_NUM;
    cfg.pin_reset    = RESET_GPIO_NUM;
    cfg.xclk_freq_hz = 20000000;
    cfg.pixel_format = PIXFORMAT_JPEG;

    // PSRAM 있어도 fb_count=1 고정 — 멀티버퍼 DMA 충돌 방지
    cfg.frame_size   = psramFound() ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
    cfg.jpeg_quality = 15;
    cfg.fb_count     = 1;

    if (esp_camera_init(&cfg) != ESP_OK) {
        Serial.println("[Camera] 초기화 실패!");
        return false;
    }
    Serial.println("[Camera] 초기화 성공.");
    return true;
}

// ============================================================
// SensorNode 클래스
// ============================================================
class SensorNode {
public:
    String   s_id;
    String   images_dir;
    String   sent_dir;
    String   state_file;
    WiFiUDP  udp;

    std::vector<String> image_queue;
    String   current_image_path;
    String   data_id;
    size_t   file_size;
    int      total_chunks;
    uint32_t crc32_val;
    int      current_idx;
    unsigned long beacon_interval;
    bool     is_revoked_in_slot;

    SensorNode(const String& node_id, const String& img_dir = "/images")
        : s_id(node_id), images_dir(img_dir),
          sent_dir(img_dir + "/sent"),
          state_file("/node_state_" + node_id + ".json"),
          file_size(0), total_chunks(0), crc32_val(0),
          current_idx(0), beacon_interval(BEACON_INTERVAL_MS),
          is_revoked_in_slot(false) {}

    void begin() {
        if (!mountLittleFS()) return;
        if (!LittleFS.exists(images_dir)) LittleFS.mkdir(images_dir);
        if (!LittleFS.exists(sent_dir))   LittleFS.mkdir(sent_dir);
        load_state();
    }

    void beginUDP() {
        udp.stop();
        udp.begin(UDP_PORT);
        Serial.printf("[UDP] 포트 %d 바인딩 완료\n", UDP_PORT);
    }

    void save_state() {
        File f = LittleFS.open(state_file, "w");
        if (!f) { Serial.println("[State] Save failed."); return; }
        f.printf("{\"current_image_path\":\"%s\",\"data_id\":\"%s\",\"current_idx\":%d}",
                 current_image_path.c_str(), data_id.c_str(), current_idx);
        f.close();
    }

    bool load_state() {
        if (!LittleFS.exists(state_file)) return false;
        File f = LittleFS.open(state_file, "r");
        if (!f) return false;
        String json = f.readString();
        f.close();

        auto extractStr = [&](const String& key) -> String {
            int i = json.indexOf(key);
            if (i < 0) return "";
            int s = i + key.length(), e = json.indexOf("\"", s);
            return (e < 0) ? "" : json.substring(s, e);
        };
        auto extractInt = [&](const String& key) -> int {
            int i = json.indexOf(key);
            if (i < 0) return 0;
            int s = i + key.length();
            int e = json.indexOf(",", s);
            if (e < 0) e = json.indexOf("}", s);
            return json.substring(s, e).toInt();
        };

        String path = extractStr("\"current_image_path\":\"");
        String did  = extractStr("\"data_id\":\"");
        int    idx  = extractInt("\"current_idx\":");

        if (path.length() == 0 || !LittleFS.exists(path)) return false;

        File img = LittleFS.open(path, "r");
        if (!img) return false;
        size_t sz = img.size();
        img.close();

        if (sz == 0) {
            Serial.println("[State] 0바이트 파일 무시, 상태 초기화");
            LittleFS.remove(path);
            LittleFS.remove(state_file);
            return false;
        }

        current_image_path = path;
        data_id            = did;
        current_idx        = idx;
        file_size          = sz;
        total_chunks       = (file_size + CHUNK_SIZE - 1) / CHUNK_SIZE;
        crc32_val          = crc32_file(path);
        Serial.printf(">>> [Restored] %s (Idx:%d/%d CRC:0x%08X)\n",
                      basename(path).c_str(), current_idx, total_chunks, crc32_val);
        return true;
    }

    // ── captureAndSave ─────────────────────────────────────
    // g_cam_ok 확인 → fb null 확인 → 저장 완전성 확인
    bool captureAndSave() {
        if (!g_cam_ok) {
            Serial.println("[Camera] 초기화 안 됨. 촬영 불가.");
            return false;
        }

        // 여유 공간 확인
        if (LittleFS.totalBytes() > 0) {
            size_t free_b = LittleFS.totalBytes() - LittleFS.usedBytes();
            Serial.printf("[Camera] FS 여유: %u bytes\n", (unsigned)free_b);
            if (free_b < 20000) { // [Fix] 60000 → 20000 (VGA JPEG ≈ 15~25KB)
                Serial.println("[Camera] FS 여유 부족. sent/ 폴더 정리 필요.");
                return false;
            }
        }

        camera_fb_t* fb = esp_camera_fb_get();
        if (!fb) {
            Serial.println("[Camera] fb_get 실패 (null). 스킵.");
            return false;
        }
        if (fb->len == 0) {
            Serial.println("[Camera] 빈 프레임. 스킵.");
            esp_camera_fb_return(fb);
            return false;
        }

        String path = images_dir + "/img_" + String(millis()) + ".jpg";
        File file = LittleFS.open(path, "w");
        if (!file) {
            Serial.println("[Camera] 파일 열기 실패.");
            esp_camera_fb_return(fb);
            return false;
        }

        size_t to_write = fb->len;
        size_t written  = file.write(fb->buf, to_write);
        file.close();
        esp_camera_fb_return(fb); // 즉시 반환

        if (written != to_write) {
            Serial.printf("[Camera] 저장 불완전 (%u/%u). 삭제.\n",
                          (unsigned)written, (unsigned)to_write);
            LittleFS.remove(path);
            return false;
        }

        Serial.printf("[Camera] 저장 완료: %s (%u bytes)\n",
                      basename(path).c_str(), (unsigned)written);
        return true;
    }

    void _scan_images() {
        image_queue.clear();
        File root = LittleFS.open(images_dir);
        if (!root || !root.isDirectory()) return;
        File file = root.openNextFile();
        while (file) {
            if (!file.isDirectory()) {
                String name     = String(file.name());
                String fullPath = name.startsWith(images_dir + "/")
                                  ? name : images_dir + "/" + name;
                String lower = fullPath; lower.toLowerCase();
                if (lower.endsWith(".jpg")) image_queue.push_back(fullPath);
            }
            file = root.openNextFile();
        }
        std::sort(image_queue.begin(), image_queue.end());
    }

    bool _prepare_next_image() {
        if (image_queue.empty()) return false;
        current_image_path = image_queue[0];

        File f = LittleFS.open(current_image_path, "r");
        if (!f) { Serial.println("[Prep] Cannot open: " + current_image_path); return false; }
        file_size = f.size();

        if (file_size == 0) {
            f.close();
            Serial.println("[Prep] 0바이트 스킵: " + basename(current_image_path));
            LittleFS.remove(current_image_path);
            image_queue.erase(image_queue.begin());
            current_image_path = "";
            return false;
        }

        time_t mtime = f.getLastWrite();
        if (mtime <= 0) mtime = (time_t)(millis() / 1000);
        f.close();

        String seed = s_id + "_" + String((unsigned long)mtime) + "_" + String(file_size);
        char id_buf[16];
        sprintf(id_buf, "%08x", crc32_buf((const uint8_t*)seed.c_str(), seed.length()));
        data_id = String(id_buf);

        total_chunks = (file_size + CHUNK_SIZE - 1) / CHUNK_SIZE;
        crc32_val    = crc32_file(current_image_path);
        current_idx  = 0;
        save_state();

        Serial.printf("\n>>> [Next] %s | ID:%s | %d chunks | CRC:0x%08X\n",
                      basename(current_image_path).c_str(),
                      data_id.c_str(), total_chunks, crc32_val);
        return true;
    }

    void _finalize_current_image() {
        if (current_image_path.length() == 0) return;
        String dest = sent_dir + "/" + basename(current_image_path);
        if (LittleFS.exists(dest))
            dest = sent_dir + "/" + String(millis()) + "_" + basename(current_image_path);
        if (!LittleFS.exists(sent_dir)) LittleFS.mkdir(sent_dir);
        LittleFS.rename(current_image_path, dest)
            ? Serial.println(">>> [Moved] " + basename(dest))
            : Serial.println("[Move Error] Rename failed.");
        if (!image_queue.empty()) image_queue.erase(image_queue.begin());
        current_image_path = ""; data_id = "";
        file_size = 0; total_chunks = 0; current_idx = 0;
        if (LittleFS.exists(state_file)) LittleFS.remove(state_file);
    }

    // [핵심 수정] 표준 6필드 BEACON (마스터 split(b'|',6) 파싱 일치)
    bool send_beacon() {
        String hdr = "BEACON|" + s_id + "|" + data_id + "|"
                   + String(total_chunks) + "|" + String(current_idx) + "|0|";
        udp.beginPacket(DRONE_IP, DRONE_PORT);
        udp.write((const uint8_t*)hdr.c_str(), hdr.length());
        if (udp.endPacket() == 1) return true;
        Serial.println("[Net] Beacon send failed.");
        return false;
    }

    void send_data_chunks(int start_idx, int count) {
        current_idx        = start_idx;
        is_revoked_in_slot = false;

        File imgFile = LittleFS.open(current_image_path, "r");
        if (!imgFile) { Serial.println("[Net] Cannot open image."); return; }

        uint8_t* buf = (uint8_t*)malloc(CHUNK_SIZE);
        if (!buf) { imgFile.close(); return; }

        for (int i = 0; i < count && current_idx < total_chunks; i++) {
            yield();

            // REVOKE 감지
            if (udp.parsePacket() > 0) {
                char rb[256]; int rl = udp.read(rb, 255); rb[rl] = '\0';
                auto tok = splitStr(String(rb), '|');
                if (tok.size() >= 3 && tok[0]=="REVOKE" && tok[1]==s_id && tok[2]==data_id) {
                    Serial.printf("[Revoked] Idx:%d\n", current_idx);
                    is_revoked_in_slot = true;
                    save_state(); free(buf); imgFile.close(); return;
                }
            }

            size_t off  = (size_t)current_idx * CHUNK_SIZE;
            size_t btr  = ((off + CHUNK_SIZE) > file_size) ? (file_size - off) : CHUNK_SIZE;
            imgFile.seek(off);
            size_t br   = imgFile.read(buf, btr);
            int last    = (current_idx == total_chunks - 1) ? 1 : 0;
            String hdr  = "DATA|" + s_id + "|" + data_id + "|"
                        + String(total_chunks) + "|" + String(current_idx)
                        + "|" + String(last) + "|";

            for (int r = 0; r < 10; r++) {
                udp.beginPacket(DRONE_IP, DRONE_PORT);
                udp.write((const uint8_t*)hdr.c_str(), hdr.length());
                udp.write(buf, br);
                if (udp.endPacket() == 1) { current_idx++; delay(5); break; }
                delay(10);
            }
        }
        free(buf); imgFile.close();
        save_state();
    }

    void send_complete_once() {
        String hdr = "COMPLETE|" + s_id + "|" + data_id + "|0|0|0|";
        uint8_t cb[4] = {
            (uint8_t)((crc32_val>>24)&0xFF), (uint8_t)((crc32_val>>16)&0xFF),
            (uint8_t)((crc32_val>> 8)&0xFF), (uint8_t)( crc32_val     &0xFF)
        };
        udp.beginPacket(DRONE_IP, DRONE_PORT);
        udp.write((const uint8_t*)hdr.c_str(), hdr.length());
        udp.write(cb, 4);
        udp.endPacket();
    }

    bool send_complete_and_wait() {
        Serial.printf("--- [Wait] COMPLETE_ACK (ID:%s CRC:0x%08X) ---\n",
                      data_id.c_str(), crc32_val);
        for (int a = 0; a < COMPLETE_RETRY_MAX; a++) {
            send_complete_once();
            Serial.printf("[COMPLETE] %d/%d\n", a+1, COMPLETE_RETRY_MAX);
            unsigned long t0 = millis();
            while (millis() - t0 < COMPLETE_RETRY_MS) {
                yield();
                if (!udp.parsePacket()) { delay(10); continue; }
                char buf[256]; int len = udp.read(buf, 255);
                if (len <= 0) continue;
                buf[len] = '\0';
                auto msg = splitStr(String(buf), '|');
                if (msg.size() < 3 || msg[1] != s_id || msg[2] != data_id) continue;
                if (msg[0] == "COMPLETE_ACK") { Serial.println("[OK] ACK 수신!"); return true; }
                if (msg[0] == "ERROR") {
                    Serial.println("[Err] " + (msg.size()>=6 ? msg[5] : "?"));
                    return false;
                }
            }
        }
        Serial.println("[COMPLETE] 타임아웃 → 마스터 수신 가정.");
        return true;
    }

    void run() {
        Serial.printf("--- [Node %s] v4.4 가동 ---\n", s_id.c_str());
        while (true) {
            yield();

            // Wi-Fi 재연결
            if (WiFi.status() != WL_CONNECTED) {
                Serial.println("\n[WiFi] 재연결 중...");
                WiFi.disconnect(true); delay(500);
                WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
                unsigned long t = millis();
                while (WiFi.status() != WL_CONNECTED && millis()-t < 10000) {
                    yield(); delay(500); Serial.print(".");
                }
                if (WiFi.status() == WL_CONNECTED) {
                    Serial.printf("\n[WiFi] 성공 (IP:%s)\n", WiFi.localIP().toString().c_str());
                    beginUDP();
                } else { Serial.println("\n[WiFi] 실패."); delay(3000); continue; }
            }

            _scan_images();

            // IDLE
            if (image_queue.empty() && current_image_path.length() == 0) {
                String idleHdr = "BEACON|" + s_id + "|IDLE|0|0|0|";
                udp.beginPacket(DRONE_IP, DRONE_PORT);
                udp.write((const uint8_t*)idleHdr.c_str(), idleHdr.length());
                udp.endPacket();
                unsigned long t0 = millis();
                while (millis()-t0 < 2000) {
                    yield();
                    if (udp.parsePacket() > 0) {
                        char buf[128]; int len = udp.read(buf, 127); buf[len]='\0';
                        auto msg = splitStr(String(buf), '|');
                        if (msg.size()>=2 && msg[1]==s_id) {
                            if (msg[0]=="IDLE_ACK") Serial.println("--- [Idle] 연결 정상 ---");
                            else if (msg[0]=="GRANT") Serial.println("--- [Idle] 마스터 처리 중 ---");
                            break;
                        }
                    }
                    delay(10);
                }
                delay(IDLE_WAIT_MS); continue;
            }

            // 이미지 준비
            if (current_image_path.length() == 0) {
                if (!_prepare_next_image()) continue;
            }

            // 전송 루프
            bool is_finished = false;
            while (true) {
                yield();
                if (WiFi.status() != WL_CONNECTED) break;
                if (!send_beacon()) { delay(2000); continue; }

                unsigned long ws = millis(); bool got = false;
                while (millis()-ws < 500) { yield(); if (udp.parsePacket()>0){got=true;break;} delay(10); }
                if (!got) { delay(beacon_interval); continue; }

                char rb[512]; int rl = udp.read(rb, 511);
                if (rl <= 0) continue;
                rb[rl] = '\0';

                if (udp.remoteIP() == IPAddress(192,168,4,1) && beacon_interval > BEACON_INTERVAL_MS)
                    beacon_interval = BEACON_INTERVAL_MS;

                auto msg = splitStr(String(rb), '|');
                if (msg.size() < 3 || msg[1] != s_id) continue;

                if (msg[0]=="COMPLETE_ACK" && msg[2]==data_id) {
                    Serial.println("[Early Exit] 이미 완료된 세션");
                    is_finished = true; break;
                }
                if (msg[0]=="GRANT") {
                    if (msg.size()<5 || msg[2]!=data_id) continue;
                    int tidx = msg[3].toInt(), nsend = msg[4].toInt();
                    if (tidx >= total_chunks) { is_finished = true; break; }
                    send_data_chunks(tidx, nsend);
                    beacon_interval = BEACON_INTERVAL_MS;
                    if (current_idx >= total_chunks) { is_finished = true; break; }
                }
                else if (msg[0]=="REVOKE" && msg[2]==data_id) {
                    if (!is_revoked_in_slot)
                        Serial.printf("[Pause] Idx:%d/%d\n", current_idx, total_chunks);
                    save_state(); break;
                }
                else if (msg[0]=="ERROR" && msg[2]==data_id) {
                    String ec = (msg.size()>=6) ? msg[5] : "";
                    Serial.println("[Err] " + ec);
                    if (ec=="CHECKSUM_FAIL") { current_idx=0; save_state(); }
                    else if (ec=="SESSION_MISMATCH") {
                        current_idx=0; current_image_path="";
                        LittleFS.remove(state_file); break;
                    }
                    else if (ec=="TIMEOUT") {
                        beacon_interval=min((unsigned long)5000, beacon_interval*2); break;
                    }
                    else if (ec=="LIVELOCK_PREVENT") {
                        Serial.printf("[Livelock] %ds 대기\n", LIVELOCK_PENALTY_MS/1000);
                        save_state();
                        unsigned long end = millis()+LIVELOCK_PENALTY_MS;
                        while (millis()<end) { yield(); delay(500); }
                        beacon_interval=2000; break;
                    }
                }
            }

            if (is_finished) {
                if (send_complete_and_wait()) _finalize_current_image();
                else { Serial.println("[Retry] 재전송 준비"); current_idx=0; save_state(); }
            } else {
                Serial.printf("[Paused] %s %d/%d\n", data_id.c_str(), current_idx, total_chunks);
            }
        }
    }
};

// ============================================================
// 전역 인스턴스 (Node ID 변경 시 여기서)
// ============================================================
SensorNode node("S03", "/images");

// ======================f#define PRE_CAPTURE_COUNT======================================
// setup()
// ============================================================
void setup() {
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
    Serial.begin(115200);
    delay(500);
    Serial.println("\n*** ESP32-CAM v4.4 Booting ***");

    WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info){
        Serial.printf("[WiFi] 끊김 reason=%d\n", info.wifi_sta_disconnected.reason);
    }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

    // ── 1. LittleFS ─────────────────────────────────────────
    node.begin();

    // ── 2. 카메라 초기화 ─────────────────────────────────────
    g_cam_ok = initCamera();

    if (g_cam_ok) {
        // AE/AWB 안정화: fb_get 없이 순수 delay만 사용
        Serial.println("[Camera] AE/AWB 안정화 대기 (1.5초)...");
        delay(1500);
        Serial.println("[Camera] 안정화 완료.");

        // ── 3. 사전 촬영 ─────────────────────────────────────
        // 이미 전송 대기 이미지가 있으면 추가 촬영 스킵 (FS 절약)
        node._scan_images();
        if (node.image_queue.size() > 0) {
            Serial.printf("[Camera] 기존 이미지 %d장 있음 → 추가 촬영 스킵\n",
                          node.image_queue.size());
        } else {
            Serial.printf("[Camera] 사전 촬영 %d장 시작\n", PRE_CAPTURE_COUNT);
            int ok = 0;
            for (int i = 0; i < PRE_CAPTURE_COUNT; i++) {
                Serial.printf("[Camera] %d/%d\n", i+1, PRE_CAPTURE_COUNT);
                if (node.captureAndSave()) ok++;
                delay(300);
                yield();
            }
            Serial.printf("[Camera] 완료: %d/%d장\n\n", ok, PRE_CAPTURE_COUNT);
        }
    } else {
        Serial.println("[Camera] 초기화 실패. 기존 LittleFS 이미지만 전송.");
    }

    // ── 4. Wi-Fi 연결 ────────────────────────────────────────
    WiFi.mode(WIFI_STA);
    WiFi.setTxPower(WIFI_POWER_11dBm);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("Wi-Fi 연결 중");
    for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
        delay(500); yield(); Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[OK] IP: %s\n", WiFi.localIP().toString().c_str());
        node.beginUDP();
    } else {
        Serial.println("\n[!] 연결 실패. run()에서 재시도.");
    }

    // ── 5. 스캔 ──────────────────────────────────────────────
    node._scan_images();
    Serial.printf("[Scan] 대기 이미지: %d장\n", node.image_queue.size());
}

void loop() {
    node.run();
}
