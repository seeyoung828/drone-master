/**
 * ESP Sensor Node C++ implementation (Arduino IDE & PlatformIO compatible)
 * Handles reliable multi-image queue transmission via UDP to the Drone Master.
 * 
 * Support: ESP32 and ESP8266 microcontrollers.
 * File System: LittleFS
 */

#include <Arduino.h>
#include <vector>
#include <algorithm>

#ifdef ESP32
#include <WiFi.h>
#include <LittleFS.h>
#elif defined(ESP8266)
#include <ESP8266WiFi.h>
#include <LittleFS.h>
#else
#error "This code is designed for ESP32 or ESP8266 microcontrollers only."
#endif

#include <WiFiUdp.h>

// --- [WiFi 및 네트워크 설정] ---
const char* WIFI_SSID = "Drone_AP";         // 드론 AP SSID
const char* WIFI_PASSWORD = "password123";  // 드론 AP 비밀번호

#define DRONE_IP "192.168.4.1"
#define DRONE_PORT 5005
#define UDP_PORT 5005
#define CHUNK_SIZE 4096  // 4KB 단위 분할 (성능 최적화)

class SensorNode {
public:
    String s_id;
    String images_dir;
    String sent_dir;
    String state_file;
    
    WiFiUDP udp;
    
    std::vector<String> image_queue;
    String current_image_path;
    String data_id;
    size_t file_size;
    int total_chunks;
    uint32_t crc32_val;
    int current_idx;
    float beacon_interval;
    bool is_revoked_in_slot; // [v3.4] 중복 REVOKE 방지 플래그
    
    SensorNode(String node_id, String img_dir = "/images") {
        s_id = node_id;
        images_dir = img_dir;
        sent_dir = img_dir + "/sent";
        state_file = "/node_state_" + s_id + ".json";
        
        current_image_path = "";
        data_id = "";
        file_size = 0;
        total_chunks = 0;
        crc32_val = 0;
        current_idx = 0;
        beacon_interval = 0.1f; // [Fix] 기본 비콘 주기
        is_revoked_in_slot = false;
    }
    
    void begin() {
        // LittleFS 파일시스템 초기화
        #ifdef ESP32
        if (!LittleFS.begin(true)) {
            Serial.println("[System Error] LittleFS Mount Failed!");
        }
        #else
        if (!LittleFS.begin()) {
            Serial.println("[System] LittleFS mount failed. Formatting...");
            LittleFS.format();
            LittleFS.begin();
        }
        #endif
        
        // 디렉토리 존재 여부 확인 및 생성
        if (!LittleFS.exists(images_dir)) {
            LittleFS.mkdir(images_dir);
        }
        if (!LittleFS.exists(sent_dir)) {
            LittleFS.mkdir(sent_dir);
        }
        
        // UDP 소켓 개방
        udp.begin(UDP_PORT);
        
        // [v3.7] 이전 상태 복구 시도
        load_state();
    }
    
    bool save_state() {
        /** [v3.7] 현재 전송 상태를 파일에 저장 (Resume 지원) */
        File f = LittleFS.open(state_file, "w");
        if (!f) {
            Serial.println("[State Error] Save failed: open file failed.");
            return false;
        }
        String json = "{\"current_image_path\":\"" + current_image_path + "\",\"data_id\":\"" + data_id + "\",\"current_idx\":" + String(current_idx) + "}";
        f.print(json);
        f.close();
        return true;
    }
    
    bool load_state() {
        /** [v3.7] 저장된 상태가 있으면 복구 */
        if (!LittleFS.exists(state_file)) {
            return false;
        }
        File f = LittleFS.open(state_file, "r");
        if (!f) {
            return false;
        }
        String json = f.readString();
        f.close();
        
        // 간단한 JSON 파싱 수행 (외부 라이브러리 의존성 차단)
        int pathIdx = json.indexOf("\"current_image_path\":\"");
        if (pathIdx != -1) {
            int pathStart = pathIdx + 22;
            int pathEnd = json.indexOf("\"", pathStart);
            current_image_path = json.substring(pathStart, pathEnd);
        }
        int idIdx = json.indexOf("\"data_id\":\"");
        if (idIdx != -1) {
            int idStart = idIdx + 11;
            int idEnd = json.indexOf("\"", idStart);
            data_id = json.substring(idStart, idEnd);
        }
        int idxIdx = json.indexOf("\"current_idx\":");
        if (idxIdx != -1) {
            int idxStart = idxIdx + 14;
            int idxEnd = json.indexOf(",", idxStart);
            if (idxEnd == -1) idxEnd = json.indexOf("}", idxStart);
            current_idx = json.substring(idxStart, idxEnd).toInt();
        }
        
        if (current_image_path.length() > 0 && LittleFS.exists(current_image_path)) {
            File imgFile = LittleFS.open(current_image_path, "r");
            if (imgFile) {
                file_size = imgFile.size();
                total_chunks = (file_size + CHUNK_SIZE - 1) / CHUNK_SIZE;
                crc32_val = calculateFileCRC32(imgFile);
                imgFile.close();
                
                Serial.print(">>> [Restored] 이전 세션 복구됨: ");
                Serial.print(getBasename(current_image_path));
                Serial.print(" (Idx: ");
                Serial.print(current_idx);
                Serial.println(")");
                return true;
            }
        }
        return false;
    }
    
    void _scan_images() {
        /** 디렉토리를 스캔하여 전송 대기 중인 이미지 목록 갱신 */
        image_queue.clear();
        
        #ifdef ESP32
        File root = LittleFS.open(images_dir);
        if (!root || !root.isDirectory()) {
            return;
        }
        File file = root.openNextFile();
        while (file) {
            if (!file.isDirectory()) {
                String name = String(file.name());
                String fullPath = name;
                // 경로 정형화
                if (!fullPath.startsWith(images_dir + "/")) {
                    fullPath = images_dir + "/" + name;
                }
                String lowerName = fullPath;
                lowerName.toLowerCase();
                if (lowerName.endsWith(".jpg")) {
                    image_queue.push_back(fullPath);
                }
            }
            file = root.openNextFile();
        }
        #else // ESP8266
        Dir dir = LittleFS.openDir(images_dir);
        while (dir.next()) {
            String name = dir.fileName();
            String fullPath = images_dir + "/" + name;
            String lowerName = fullPath;
            lowerName.toLowerCase();
            if (lowerName.endsWith(".jpg")) {
                image_queue.push_back(fullPath);
            }
        }
        #endif
        
        // 파일명 기준 정렬 (보통 타임스탬프 포함)
        std::sort(image_queue.begin(), image_queue.end());
    }
    
    bool _prepare_next_image() {
        /** 큐에서 다음 이미지를 꺼내어 전송 준비 (pop 하지 않고 참조만 수행) */
        if (image_queue.empty()) {
            return false;
        }
        
        // [v3.2] pop(0) 대신 참조만 수행. 성공 시에만 _finalize_current_image에서 제거.
        current_image_path = image_queue[0];
        
        File f = LittleFS.open(current_image_path, "r");
        if (!f) {
            Serial.println("[Error] Failed to open image file: " + current_image_path);
            return false;
        }
        file_size = f.size();
        
        time_t mtime = 0;
        #if defined(ESP32) || defined(ESP8266)
        mtime = f.getLastWrite();
        #endif
        if (mtime <= 0) {
            mtime = (time_t)millis();
        }
        f.close();
        
        // [v3.3] Data_ID 생성 고도화: S_ID + mtime + fsize 조합 (노드 간 충돌 원천 차단)
        String seed = s_id + "_" + String((unsigned long)mtime) + "_" + String(file_size);
        uint32_t seed_crc = calculateBufferCRC32((const uint8_t*)seed.c_str(), seed.length());
        
        char id_buf[16];
        sprintf(id_buf, "%08x", seed_crc);
        data_id = String(id_buf);
        
        total_chunks = (file_size + CHUNK_SIZE - 1) / CHUNK_SIZE;
        
        File f_crc = LittleFS.open(current_image_path, "r");
        crc32_val = calculateFileCRC32(f_crc);
        f_crc.close();
        
        current_idx = 0;
        save_state(); // 신규 이미지 준비 시 상태 저장
        return true;
    }
    
    void _finalize_current_image() {
        /** 전송이 완료된 이미지를 큐에서 제거하고 폴더 이동 */
        if (current_image_path.length() == 0) {
            return;
        }
        
        String base = getBasename(current_image_path);
        String dest_path = sent_dir + "/" + base;
        
        if (LittleFS.exists(dest_path)) {
            dest_path = sent_dir + "/" + String(millis()) + "_" + base;
        }
        
        // 폴더가 존재하지 않는 경우 생성
        if (!LittleFS.exists(sent_dir)) {
            LittleFS.mkdir(sent_dir);
        }
        
        if (LittleFS.rename(current_image_path, dest_path)) {
            Serial.print(">>> [Moved] ");
            Serial.print(getBasename(dest_path));
            Serial.println(" -> sent/");
        } else {
            Serial.println("[Move Error] Rename failed.");
        }
        
        // 성공한 경우에만 큐에서 제거
        if (!image_queue.empty()) {
            image_queue.erase(image_queue.begin());
        }
        
        // 상태 초기화
        current_image_path = "";
        data_id = "";
        file_size = 0;
        total_chunks = 0;
        current_idx = 0;
        
        // 상태 파일 삭제
        if (LittleFS.exists(state_file)) {
            LittleFS.remove(state_file);
        }
    }
    
    bool send_beacon() {
        /** 드론에게 자신의 상태를 알림 (성공 여부 반환) */
        String header = "BEACON|" + s_id + "|" + data_id + "|" + String(total_chunks) + "|" + String(current_idx) + "|0|";
        
        udp.beginPacket(DRONE_IP, DRONE_PORT);
        udp.write((const uint8_t*)header.c_str(), header.length());
        if (udp.endPacket() == 1) {
            return true;
        } else {
            Serial.println("[Network Error] Beacon send failed.");
            return false;
        }
    }
    
    void send_data_chunks(int start_idx, int count) {
        /** 요청받은 개수만큼 데이터 전송 (REVOKE 감지 로직 추가) */
        current_idx = start_idx;
        is_revoked_in_slot = false; // [v3.4] 슬롯 시작 시 플래그 초기화
        
        File imgFile = LittleFS.open(current_image_path, "r");
        if (!imgFile) {
            Serial.println("[Error] Failed to open image file for chunk sending.");
            return;
        }
        
        uint8_t* chunk_buf = (uint8_t*)malloc(CHUNK_SIZE);
        if (!chunk_buf) {
            Serial.println("[Error] Out of memory for chunk buffer!");
            imgFile.close();
            return;
        }
        
        for (int i = 0; i < count; i++) {
            if (current_idx >= total_chunks) {
                break;
            }
            
            // 1. 마스터의 중단 명령(REVOKE) 수신 확인 (비차단 체크)
            int checkPacket = udp.parsePacket();
            if (checkPacket > 0) {
                char recvBuf[256];
                int len = udp.read(recvBuf, sizeof(recvBuf) - 1);
                if (len > 0) {
                    recvBuf[len] = '\0';
                    std::vector<String> msgTokens = splitString(String(recvBuf), '|');
                    if (msgTokens.size() >= 3 && msgTokens[0] == "REVOKE" && msgTokens[1] == s_id && msgTokens[2] == data_id) {
                        Serial.print("\n[Revoked] 마스터에 의해 전송이 중단되었습니다. (ID: ");
                        Serial.print(data_id);
                        Serial.println(")");
                        is_revoked_in_slot = true; // [v3.4] 중단 플래그 설정
                        save_state(); // 중단 시점 저장
                        free(chunk_buf);
                        imgFile.close();
                        return; // 전송 즉시 중단 및 루프 탈출
                    }
                }
            }
            
            // 2. 데이터 청크 전송
            size_t start_offset = (size_t)current_idx * CHUNK_SIZE;
            imgFile.seek(start_offset);
            size_t bytes_to_read = (start_offset + CHUNK_SIZE > file_size) ? (file_size - start_offset) : CHUNK_SIZE;
            
            size_t bytes_read = imgFile.read(chunk_buf, bytes_to_read);
            int last_flag = (current_idx == total_chunks - 1) ? 1 : 0;
            
            String header = "DATA|" + s_id + "|" + data_id + "|" + String(total_chunks) + "|" + String(current_idx) + "|" + String(last_flag) + "|";
            
            // [v3.4] Windows 송신 버퍼 오버플로우 대응 재시도 로직 강화 -> ESP UDP 송신 검증 재시도 로직 반영
            int retry_count = 0;
            bool send_success = false;
            while (retry_count < 10) {
                udp.beginPacket(DRONE_IP, DRONE_PORT);
                udp.write((const uint8_t*)header.c_str(), header.length());
                udp.write(chunk_buf, bytes_read);
                if (udp.endPacket() == 1) {
                    send_success = true;
                    current_idx++;
                    delay(5); // time.sleep(0.005)
                    break; // 성공 시 탈출
                } else {
                    delay(10); // time.sleep(0.01) 대기 시간 상향 대응
                    retry_count++;
                }
            }
            
            if (!send_success) {
                Serial.println("[Network Error] Data chunk send failed.");
                free(chunk_buf);
                imgFile.close();
                return;
            }
        }
        
        free(chunk_buf);
        imgFile.close();
        
        // 한 번의 GRANT 루프가 끝나면 상태 저장
        save_state();
    }
    
    void send_complete() {
        /** 전송 완료 및 체크섬 보고 */
        String header = "COMPLETE|" + s_id + "|" + data_id + "|0|0|0|";
        
        uint8_t checksum_bin[4];
        checksum_bin[0] = (uint8_t)((crc32_val >> 24) & 0xFF);
        checksum_bin[1] = (uint8_t)((crc32_val >> 16) & 0xFF);
        checksum_bin[2] = (uint8_t)((crc32_val >> 8) & 0xFF);
        checksum_bin[3] = (uint8_t)(crc32_val & 0xFF);
        
        udp.beginPacket(DRONE_IP, DRONE_PORT);
        udp.write((const uint8_t*)header.c_str(), header.length());
        udp.write(checksum_bin, 4);
        udp.endPacket();
        
        Serial.print("--- [SUCCESS] ");
        Serial.print(s_id);
        Serial.print(" 전송 완료 보고 (ID: ");
        Serial.print(data_id);
        Serial.print(", CRC32: 0x");
        Serial.print(crc32_val, HEX);
        Serial.println(") ---");
    }
    
    void run() {
        Serial.print("--- [Node ");
        Serial.print(s_id);
        Serial.println("] Multi-Image Queue 가동 (v3.9 Handshake-based Monitoring) ---");
        
        while (true) {
            yield(); // Watchdog 타이머 리셋 및 백그라운드 처리 유도
            
            // WiFi 연결 유지 처리
            if (WiFi.status() != WL_CONNECTED) {
                Serial.println("\n[WiFi] Connection lost. Reconnecting...");
                WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
                unsigned long wifiStart = millis();
                while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 8000) {
                    delay(500);
                    Serial.print(".");
                }
                if (WiFi.status() == WL_CONNECTED) {
                    Serial.println("\n[WiFi] Reconnected!");
                } else {
                    delay(2000);
                    continue;
                }
            }
            
            _scan_images();
            
            // [v3.9] 전송할 이미지가 없는 경우 (Handshake-based Monitoring)
            if (image_queue.empty() && current_image_path.length() == 0) {
                data_id = "IDLE";
                total_chunks = 0;
                current_idx = 0;
                
                if (send_beacon()) {
                    // 마스터의 응답(IDLE_ACK)을 기다려 실제 연결 여부 확인 (Deaf Loop 방지)
                    unsigned long startWait = millis();
                    bool got_response = false;
                    while (millis() - startWait < 2000) {
                        yield();
                        int size = udp.parsePacket();
                        if (size > 0) {
                            char recvBuf[256];
                            int len = udp.read(recvBuf, sizeof(recvBuf) - 1);
                            if (len > 0) {
                                recvBuf[len] = '\0';
                                std::vector<String> msg = splitString(String(recvBuf), '|');
                                if (msg.size() >= 2) {
                                    if (msg[0] == "IDLE_ACK" && msg[1] == s_id) {
                                        Serial.println("--- [Waiting] 전송할 이미지가 없습니다. (연결 정상) ---");
                                        got_response = true;
                                        break;
                                    } else if (msg[0] == "GRANT") {
                                        // 마스터가 아직 이전 세션을 종료하지 않았을 경우 대응
                                        Serial.println("--- [Waiting] 마스터가 아직 이전 세션을 처리 중입니다... ---");
                                        got_response = true;
                                        break;
                                    }
                                }
                            }
                        }
                        delay(10);
                    }
                    if (!got_response) {
                        Serial.println("[Network Error] 드론으로부터 응답이 없습니다. (연결 유실 가능성)");
                    }
                } else {
                    Serial.println("[Network Error] 드론 AP와 연결되지 않았습니다. (송신 실패)");
                }
                
                delay(2000); // 5초 -> 2초로 단축하여 반응성 향상
                continue;
            }
            
            // [v3.2] 현재 전송 중인 이미지가 없으면 새로 준비
            if (current_image_path.length() == 0) {
                if (!_prepare_next_image()) {
                    continue;
                }
                Serial.print("\n>>> [Next Image] ");
                Serial.print(getBasename(current_image_path));
                Serial.print(" (ID: ");
                Serial.print(data_id);
                Serial.print(", ");
                Serial.print(total_chunks);
                Serial.println(" chunks)");
            }
            
            // Inner Loop: 단일 이미지 전송 루프 (Resume 지원)
            bool is_finished = false;
            while (true) {
                yield();
                
                if (WiFi.status() != WL_CONNECTED) {
                    break;
                }
                
                // [v3.6.1] 비콘 전송 실패 시 대기 후 재시도
                if (!send_beacon()) {
                    delay(2000);
                    continue;
                }
                
                // 마스터 응답 대기 (0.5초 타임아웃)
                unsigned long startWait = millis();
                bool got_packet = false;
                while (millis() - startWait < 500) {
                    yield();
                    if (udp.parsePacket() > 0) {
                        got_packet = true;
                        break;
                    }
                    delay(10);
                }
                
                if (got_packet) {
                    char recvBuf[512];
                    int len = udp.read(recvBuf, sizeof(recvBuf) - 1);
                    if (len > 0) {
                        recvBuf[len] = '\0';
                        
                        // [v3.7] 가속 핸드셰이크 (Promiscuous Listening)
                        // 발신자가 드론 IP 이면 interval 단축
                        if (udp.remoteIP() == IPAddress(192, 168, 4, 1)) {
                            if (beacon_interval > 0.1f) {
                                Serial.println("[Accelerated] Drone signal detected. Resetting back-off interval.");
                                beacon_interval = 0.1f;
                            }
                        }
                        
                        std::vector<String> msg = splitString(String(recvBuf), '|');
                        if (msg.size() >= 3 && msg[1] == s_id) {
                            
                            // [v3.6.1] Early Exit 대응: 마스터가 이미 완료한 세션인 경우
                            if (msg[0] == "COMPLETE_ACK" && msg[2] == data_id) {
                                Serial.print("\n[Early Exit] 마스터가 이미 완료한 이미지입니다. (ID: ");
                                Serial.print(data_id);
                                Serial.println(")");
                                is_finished = true;
                                break;
                            }
                            
                            if (msg[0] == "GRANT") {
                                if (msg[2] != data_id) {
                                    continue;
                                }
                                
                                int target_idx = msg[3].toInt();
                                if (target_idx >= total_chunks) {
                                    is_finished = true;
                                    break; // 전송 완료
                                }
                                
                                int num_to_send = msg[4].toInt();
                                send_data_chunks(target_idx, num_to_send);
                            }
                            else if (msg[0] == "REVOKE" && msg[2] == data_id) {
                                // [v3.4] 중복 로그 방지: send_data_chunks에서 이미 출력했다면 스킵
                                if (!is_revoked_in_slot) {
                                    Serial.print("\n[Pause] 슬롯 시간이 종료되어 전송이 일시 중단되었습니다. (ID: ");
                                    Serial.print(data_id);
                                    Serial.println(")");
                                }
                                break; // 루프 탈출하여 비콘 대기 상태로 복귀 (Resume 준비)
                            }
                            else if (msg[0] == "ERROR" && msg[2] == data_id) {
                                String error_code = msg.size() >= 6 ? msg[5] : "";
                                Serial.print("[Error Received] Code: ");
                                Serial.println(error_code);
                                
                                if (error_code == "CHECKSUM_FAIL") {
                                    current_idx = 0;
                                    save_state();
                                }
                                else if (error_code == "SESSION_MISMATCH") {
                                    current_idx = 0;
                                    current_image_path = ""; // 세션 정보 초기화하여 재시작 유도
                                    if (LittleFS.exists(state_file)) {
                                        LittleFS.remove(state_file);
                                    }
                                    break;
                                }
                                else if (error_code == "TIMEOUT") {
                                    beacon_interval = (beacon_interval * 2.0f > 5.0f) ? 5.0f : (beacon_interval * 2.0f);
                                    break;
                                }
                                else if (error_code == "LIVELOCK_PREVENT") {
                                    Serial.print("[Livelock] 진행 중단 감지. 세션을 초기화하고 백오프를 실행합니다. (ID: ");
                                    Serial.print(data_id);
                                    Serial.println(")");
                                    current_idx = 0;
                                    save_state();
                                    delay(2000); // 즉각적인 재접속 방지
                                    beacon_interval = 2.0f;
                                    break;
                                }
                            }
                        }
                    }
                    beacon_interval = 0.1f;
                } else {
                    // [v3.7] 백오프 상태에서 listen을 계속 수행
                    delay((unsigned long)(beacon_interval * 1000.0f));
                    continue;
                }
            }
            
            // [v3.3] 정상 종료 시에만 COMPLETE 송신 및 검증 대기
            if (is_finished) {
                send_complete();
                
                // [v3.3] Wait-for-Verdict 상태: 마스터의 최종 판정을 기다림
                Serial.print("--- [Wait] 마스터의 수집 확정(ACK)을 기다리는 중... (ID: ");
                Serial.print(data_id);
                Serial.println(") ---");
                
                bool verdict_received = false;
                unsigned long wait_start = millis();
                
                while (millis() - wait_start < 3000) {
                    yield();
                    if (udp.parsePacket() > 0) {
                        char checkBuf[256];
                        int len = udp.read(checkBuf, sizeof(checkBuf) - 1);
                        if (len > 0) {
                            checkBuf[len] = '\0';
                            std::vector<String> msg = splitString(String(checkBuf), '|');
                            if (msg.size() >= 3 && msg[1] == s_id && msg[2] == data_id) {
                                if (msg[0] == "COMPLETE_ACK") {
                                    Serial.println("[Verdict] 수집 성공 확정 (COMPLETE_ACK 수신)");
                                    verdict_received = true;
                                    break;
                                } else if (msg[0] == "ERROR") {
                                    Serial.print("[Verdict] 수집 실패 보고 (Code: ");
                                    if (msg.size() >= 6) {
                                        Serial.print(msg[5]);
                                    }
                                    Serial.println(")");
                                    is_finished = false; // 다시 전송 시도 루프로 돌아가도록 처리
                                    break;
                                }
                            }
                        }
                    }
                    delay(10); // [v3.6] 0.2s -> 0.1s 응답 감도 향상
                }
                
                // 판정이 성공적이거나 대기 시간이 종료되면 정리
                if (is_finished) {
                    _finalize_current_image();
                } else {
                    Serial.println("--- [Retry] 검증 실패로 인해 세션을 유지합니다. ---");
                }
            } else {
                // REVOKE 등으로 인한 일시 중단 시에는 아무것도 하지 않고 다음 BEACON 루프로 돌아감
                Serial.print("--- [Paused] ");
                Serial.print(s_id);
                Serial.print(" 전송 일시 중단 (ID: ");
                Serial.print(data_id);
                Serial.print(", Progress: ");
                Serial.print(current_idx);
                Serial.print("/");
                Serial.print(total_chunks);
                Serial.println(") ---");
            }
        }
    }
    
    // --- [Helper Methods] ---
    
    std::vector<String> splitString(const String &str, char delimiter) {
        std::vector<String> tokens;
        int start = 0;
        int end = str.indexOf(delimiter);
        while (end != -1) {
            tokens.push_back(str.substring(start, end));
            start = end + 1;
            end = str.indexOf(delimiter, start);
        }
        if (start < (int)str.length()) {
            tokens.push_back(str.substring(start));
        }
        return tokens;
    }
    
    String getBasename(const String &path) {
        int idx = path.lastIndexOf('/');
        if (idx == -1) return path;
        return path.substring(idx + 1);
    }
    
    uint32_t calculateBufferCRC32(const uint8_t* buf, size_t len) {
        uint32_t crc = 0xFFFFFFFF;
        for (size_t i = 0; i < len; i++) {
            crc ^= buf[i];
            for (int j = 0; j < 8; j++) {
                if (crc & 1) {
                    crc = (crc >> 1) ^ 0xEDB88320;
                } else {
                    crc >>= 1;
                }
            }
        }
        return ~crc;
    }
    
    uint32_t calculateFileCRC32(File &file) {
        uint32_t crc = 0xFFFFFFFF;
        file.seek(0);
        uint8_t buf[256];
        while (file.available()) {
            size_t n = file.read(buf, sizeof(buf));
            for (size_t i = 0; i < n; i++) {
                crc ^= buf[i];
                for (int j = 0; j < 8; j++) {
                    if (crc & 1) {
                        crc = (crc >> 1) ^ 0xEDB88320;
                    } else {
                        crc >>= 1;
                    }
                }
            }
        }
        return ~crc;
    }
    
    void create_dummy_images_if_empty() {
        /** [테스트 지원 편의기능] images 디렉토리가 완전히 비어있는 경우 더미 파일 자동 생성 */
        _scan_images();
        if (image_queue.empty()) {
            Serial.println("[System] images/ directory is empty. Generating dummy JPEG files for testing...");
            
            // 더미 이미지 1 생성
            File f1 = LittleFS.open(images_dir + "/test01.jpg", "w");
            if (f1) {
                uint8_t header[] = {0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01};
                f1.write(header, sizeof(header));
                for (int i = 0; i < 20000; i++) {
                    f1.write((uint8_t)random(0, 256));
                }
                uint8_t eof[] = {0xFF, 0xD9};
                f1.write(eof, sizeof(eof));
                f1.close();
                Serial.println("[System] Generated " + images_dir + "/test01.jpg (20KB)");
            }
            
            // 더미 이미지 2 생성
            File f2 = LittleFS.open(images_dir + "/test02.jpg", "w");
            if (f2) {
                uint8_t header[] = {0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01};
                f2.write(header, sizeof(header));
                for (int i = 0; i < 35000; i++) {
                    f2.write((uint8_t)random(0, 256));
                }
                uint8_t eof[] = {0xFF, 0xD9};
                f2.write(eof, sizeof(eof));
                f2.close();
                Serial.println("[System] Generated " + images_dir + "/test02.jpg (35KB)");
            }
            
            // 큐 다시 로드
            _scan_images();
        }
    }
};

// --- [전역 SensorNode 객체 인스턴스화] ---
// 기본 Node ID: S01, 이미지 디렉토리: /images
SensorNode node("S01", "/images");

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n*** ESP Sensor Node Booting ***");
    
    // WiFi 연결 시도
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("Connecting to SSID: ");
    Serial.println(WIFI_SSID);
    
    int wifi_retries = 0;
    while (WiFi.status() != WL_CONNECTED && wifi_retries < 30) {
        delay(500);
        Serial.print(".");
        wifi_retries++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi Connected successfully!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nWiFi Connection failed. Will retry in background...");
    }
    
    // 센서 노드 작동 개시
    node.begin();
    
    // 대기 이미지가 전혀 없으면 더미 JPEGs를 생성하여 즉시 가동 가능케 함 (테스트 목적)
    node.create_dummy_images_if_empty();
}

void loop() {
    // 센서 노드 메인 루프 가동
    node.run();
}
