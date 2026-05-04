# 🚀 마스터 주도형 RSSI 트래킹 기반 속도 저하 해결 방안 (v3.0)

`slow_image_problem.md`에서 분석된 Windows 노드의 RSSI 측정 병목 문제를 근본적으로 해결하기 위해, RSSI 측정 책임을 마스터(Raspberry Pi)로 이전하고 통신 프로토콜을 최적화하는 전수 수정 계획을 정의합니다.

## 1. 개요 및 변경 원칙
- **측정 주체 변경:** Slave(Windows)의 `netsh` 호출을 폐지하고, Master(Pi)의 `iw station dump`를 사용함.
- **프로토콜 슬림화:** 패킷 헤더에서 `RSSI` 필드를 제거하여 전송 오버헤드 및 노드 연산 부하 최소화.
- **아키텍처 분리:** 전송(Data Flow)과 제어(Control Flow - RSSI 측정)를 물리적으로 분리함.

---

## 2. 세부 파일 수정 계획 (Total 6 Files)

### 2.1 [문서] docs/protocol_spec.md & docs/scheduling_policy.md
- **수정 내용:** 패킷 구조에서 `RSSI` 필드 삭제 및 프로토콜 버전 v3.0 상향.
- **영향:** 전체 시스템의 메시지 규격이 7개 필드에서 6개 필드로 변경됨을 명시.
- **스케줄링 정책:** RSSI 기반 점수 계산 방식은 유지하되, 데이터 소스가 "Master-Side Cache"임을 명시함.

### 2.2 [공통] server_udp/common/utility.py
- **수정 내용:** 
    - `HEADER_FIELDS_COUNT`를 `7`에서 `6`으로 변경.
    - `parse_packet()` 함수에서 `parts[5]`(기존 RSSI)를 제거하고 인덱스 재배열.
- **영향:** 마스터와 노드 양쪽에서 패킷 파싱 시 오류 방지.

### 2.3 [노드] server_udp/esp/sensor_node.py (및 esp1/sensor_node.py)
- **수정 내용:**
    - `get_rssi()` 함수 호출 및 관련 이동 평균 로직 삭제.
    - `send_beacon()`, `send_data_chunks()`, `send_complete()`에서 생성하는 패킷 문자열(f-string)에서 `rssi` 변수 제거.
- **결과:** 청크 전송 사이의 지연(0.5s~1.0s)이 완전히 제거되어 초고속 전송 가능.

### 2.4 [마스터] server_udp/pi/drone_master.py
- **수정 내용 (매우 중요):**
    1.  **RSSI 캐시 시스템:** `rssi_cache = {}` (MAC 주소를 키로 저장) 생성.
    2.  **백그라운드 스레드:** `iw station dump`를 주기적으로 실행하여 MAC-Signal 맵을 갱신하는 루틴 추가.
    3.  **IP-MAC 매핑:** `/proc/net/arp`를 파싱하여 수신된 IP(`addr[0]`)를 MAC 주소로 변환하는 유틸리티 추가.
    4.  **패킷 수신 로직:** `sock.recvfrom` 이후 `parts[5]` 파싱 로직을 제거하고, 대신 `rssi_cache`에서 해당 노드의 값을 조회하여 `sensors_mem`에 저장.
    5.  **IDLE_TIMEOUT 조정:** 안정성을 위해 `0.5` -> `1.2`로 유지 또는 상향.

---

## 3. 구현 핵심 로직 (Master-Side)

### A. MAC 주소 추출 및 RSSI 매핑
```python
def get_mac_rssi_map():
    # iw station dump 결과 파싱
    # Output: {'e1:7e:8b:cf:d4:a7': -33, ...}
    pass

def get_ip_mac_map():
    # /proc/net/arp 파싱
    # Output: {'192.168.4.10': 'e1:7e:8b:cf:d4:a7', ...}
    pass
```

### B. 스케줄러 통합
`current_rssi = rssi_cache.get(node_mac, -100)`  
위와 같이 마스터 내부 메모리에서 값을 가져와 `calculate_score` 및 `get_dynamic_n`에 전달함.

---

## 4. 기대 효과 및 검증
- **성능:** `test_result.txt`에서 확인된 `Chunks: 0` 현상이 해결되고, 이론상 최대 처리량인 슬롯당 40개 청크 도달 가능.
- **안정성:** 노드의 OS(Windows/Linux)와 관계없이 일관된 신호 측정 및 스케줄링 보장.

이 계획은 노드와 마스터 간의 긴밀한 협력이 필요하므로, 모든 파일을 동시 또는 순차적으로 신속하게 수정해야 합니다.
