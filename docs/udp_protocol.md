# UDP Application-Layer Protocol

## 1. 개요

본 프로젝트에서는 ESP32-CAM 센서 노드가 촬영한 이미지를 짧은 접촉 시간 안에 효율적으로 전송하기 위해 UDP 기반 응용 계층 전송 프로토콜을 설계하였다.

UDP는 TCP보다 가볍고 빠르지만, 패킷 손실이나 순서 보장을 제공하지 않는다. 따라서 본 시스템에서는 UDP 위에서 BEACON, GRANT, DATA, COMPLETE, ERROR 메시지를 정의하여 전송 흐름을 제어한다.

## 2. 메시지 종류

| 메시지 | 방향 | 역할 |
|---|---|---|
| BEACON | Sensor → Master | 센서가 전송 가능한 데이터가 있음을 알림 |
| GRANT | Master → Sensor | 특정 센서에게 전송 권한 부여 |
| DATA | Sensor → Master | 이미지 chunk 전송 |
| COMPLETE | Sensor → Master | 이미지 전송 완료 알림 및 CRC 전달 |
| ERROR | Master → Sensor | CRC 실패, 세션 불일치, 타임아웃 등 오류 알림 |

## 3. 기본 전송 흐름

```text
Sensor Node                  Master Node
    | ---- BEACON --------> |
    | <---- GRANT --------- |
    | ---- DATA ----------> |
    | ---- DATA ----------> |
    | ---- DATA ----------> |
    | ---- COMPLETE ------> |
    | <---- ERROR --------- |  실패 시
