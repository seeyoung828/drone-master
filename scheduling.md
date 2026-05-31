# 🛰️ 스케줄링 공식 정밀화 및 가중치 최적화 구현 가이드 (Scheduling Formula Refinement & Weight Optimization)

본 문서는 `project_progress.md`의 핵심 과제인 **"스케줄링 공식 정밀화 및 가중치 최적화"**를 실제 코드 개발 환경에 적용하기 위한 상세 진행 절차 및 설계 표준을 정의합니다.

---

## 1. 개요 및 배경 (Overview & Background)

현재 드론 마스터(`drone_master.py`)가 적용 중인 **선형 스케줄링 공식**은 통신 환경과 세션 상태의 복합적인 상호작용을 완벽하게 반영하지 못하는 한계가 있습니다. 이를 논문에서 제안된 **비선형 스케줄링 공식**으로 고도화하고, 그리드 서치(Grid Search)로 검증된 최적의 가중치를 적용하여 AoI(Age of Information)를 최소화하고 수집 효율성을 극대화합니다.

### 1.1 기존 선형 공식 vs 신규 비선형 공식 비교

*   **기존 선형 공식 (v3.7):**
    $$Score = (0.5 \times NormAging) + (0.3 \times NormCompletion) + (0.2 \times NormRSSI)$$
    *   *한계점:* RSSI의 변동성이 점수에 선형적으로만 반영되어 신호가 극도로 좋거나 나쁜 경계선에서의 의사결정이 모호함. 또한, 완료 직전의 노드를 빠르게 해소하여 세션을 종료(Early Exit)시키는 가속 효과가 부족함.
*   **신규 비선형 공식 (v4.0 제안):**
    $$Score = (W_1 \times \tanh(NormRSSI)) + \exp(W_2 \times Remaining) + (W_3 \times NormAging)$$
    *   *최적 가중치 (Grid Search 도출):* $W_1 = 0.26$, $W_2 = 0.48$, $W_3 = 0.26$
    *   *핵심 개선 사항:*
        1.  **$\tanh(NormRSSI)$ 도입:** RSSI 수치가 매우 높을 때 점수의 포화(Saturation) 현상을 모사하여 불필요한 과적합을 방지하고, 신호 한계점 부근에서의 가파른 점수 하락을 반영.
        2.  **$\exp(W_2 \times Remaining)$ 도입:** 완료 시점이 다가올수록 점수에 지수적 가산점(Exponential Boost)을 부여하여 "거의 다 수집된 세션"을 신속하게 마무리함으로써 미완성 세션의 잔류를 방지.

---

## 2. 수학적 모델 정의 및 정규화 세부사항

비선형 공식을 코드로 안정적으로 구현하기 위해 각 구성 요소의 입력 범위를 정규화하고 예외 상황을 처리하는 설계안입니다.

### 2.1 RSSI 정규화 및 $\tanh$ 매핑 (RSSI Term)
*   **원시 데이터 범위:** $-100 \text{ dBm}$ (한계 신호) ~ $-30 \text{ dBm}$ (최상 신호)
*   **정규화 공식:**
    $$NormRSSI = \max\left(0, \min\left(1, \frac{RSSI + 100}{70}\right)\right)$$
*   **비선형 변환:** $\tanh(NormRSSI)$
    *   $NormRSSI = 0$ 일 때 $\tanh(0) = 0$
    *   $NormRSSI = 1$ 일 때 $\tanh(1) \approx 0.761$
    *   신호 강도가 우수할 때 점수의 급격한 증가를 다소 둔화시켜, Aging이나 Remaining 등 다른 주요 변수들과의 균형을 유지합니다.

### 2.2 남은 데이터 분량의 지수 가중치 매핑 (Remaining Term)
세션을 신속하게 종결하여 AoI를 개선하기 위해 '완료도(Completion)' 또는 '잔여량(Remaining)'에 지수 함수를 결합합니다. 논문상의 공식 명칭은 $Remaining$이지만, 기존 시스템의 목표인 "많이 수집된 세션을 먼저 끝내기"를 달성하기 위해 **정규화된 완료율($NormCompletion$)**을 지수 함수의 인자로 활용합니다.
*   **정규화 공식:**
    $$NormCompletion = \frac{Current\_Chunk}{Total\_Chunks} \quad (\text{단, } Total\_Chunks > 0)$$
    *   *예외 처리:* 전송할 이미지가 없는 유휴 노드이거나 정보가 아직 수집되지 않은 경우($Total\_Chunks = 0$), 기본값 $0.0$으로 처리합니다.
*   **비선형 변환:** $\exp(W_2 \times NormCompletion)$
    *   $NormCompletion = 0$ (시작 안 됨) 일 때 $\exp(0) = 1.0$ (지수 가산 없음)
    *   $NormCompletion = 1.0$ (완료 직전) 일 때 $\exp(0.48) \approx 1.616$ (약 $+0.616$ 가산점 부여)
    *   이 장치는 여러 노드가 동시에 경쟁할 때, 거의 수집이 끝난 노드의 세션을 집중적으로 후속 수집하여 신속하게 병목을 해소시킵니다.

### 2.3 미접촉 시간의 선형 매핑 (Aging Term)
기아 현상(Starvation)을 방지하기 위해 미접촉 시간(Aging)은 선형 정규화를 유지하되, 정밀한 가중치를 부여합니다.
*   **정규화 공식:**
    $$NormAging = \min\left(1.0, \frac{T_{now} - T_{last\_seen}}{AGING\_THRESHOLD}\right)$$
    *   `AGING_THRESHOLD`는 기존 설정값인 `60.0초`를 유지합니다.

---

## 3. 코드 수정 상세 설계 (Code Modification Design)

수정이 필요한 대상 파일은 `server_udp/pi/drone_master.py`입니다. 비선형 계산을 안정적으로 수행하기 위해 표준 `math` 라이브러리를 활용합니다.

### 3.1 라이브러리 임포트 및 최적 가중치 정의
```python
# C:\Users\user\OneDrive\바탕 화면\26.1학기\종프\drone-master\server_udp\pi\drone_master.py
# (파일 상단 임포트 영역에 math 추가 및 가중치 상수 정의)

import math

# --- [최적화된 스케줄링 가중치 (Grid Search 결과)] ---
W1_RSSI = 0.26      # RSSI (tanh 적용)
W2_COMP = 0.48      # Completion (exp 적용)
W3_AGING = 0.26     # Aging (선형)
```

### 3.2 `calculate_score` 함수 개편안
```python
def calculate_score(s_id, info):
    """
    [v4.0] 비선형 스케줄링 공식 적용
    Score = (W1 * tanh(NormRSSI)) + exp(W2 * NormCompletion) + (W3 * NormAging)
    최적 가중치: W1 = 0.26, W2 = 0.48, W3 = 0.26
    """
    now = time.time()
    if info.get('timeout_until', 0) > now:
        return 0.0

    # 1. RSSI Score (W1 = 0.26, tanh 적용)
    rssi = get_node_rssi(info['addr'][0])
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    rssi_term = math.tanh(norm_rssi)  # 비선형 포화 곡선 매핑

    # 2. Completion Score (W2 = 0.48, exp 적용)
    total = info.get('total', 0)
    curr = info.get('curr', 0)
    norm_completion = curr / total if total > 0 else 0.0
    exp_term = math.exp(W2_COMP * norm_completion)  # 완료 직전 노드 집중 가속

    # 3. Aging Score (W3 = 0.26, 기아 방지)
    wait_time = now - info['last_seen']
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)

    # 4. 종합 점수 산출
    score = (W1_RSSI * rssi_term) + exp_term + (W3_AGING * norm_aging)
    
    # 디버깅 편의를 위해 개별 항목 세부 점수 로깅 준비 (dprint 활용 가능)
    # dprint(f"[Score Calc] {s_id} -> Tot: {score:.3f} (RSSI: {rssi_term:.3f}, Exp: {exp_term:.3f}, Aging: {norm_aging:.3f})")
    
    return score
```

---

## 4. 독립형 시뮬레이션 및 검증 도구 개발 (Verification & Simulation)

실제 통신 네트워크를 켜지 않고도, 다양한 시나리오상에서 기존 공식과 신규 공식의 선택 결과 차이를 시각적으로 증명할 수 있는 **스케줄링 시뮬레이터 스크립트**(`scratch/test_nonlinear_scheduling.py`)를 개발합니다.

### 4.1 시뮬레이터 소스 코드 구성안

```python
# scratch/test_nonlinear_scheduling.py
# (임시 검증을 위해 workspace/scratch 폴더 아래에 생성)

import math
import time

# 구 공식 가중치
OLD_W_AGING = 0.5
OLD_W_COMP = 0.3
OLD_W_RSSI = 0.2

# 신 공식 가중치
W1_RSSI = 0.26
W2_COMP = 0.48
W3_AGING = 0.26
AGING_THRESHOLD = 60.0

def get_old_score(rssi, total, curr, wait_time):
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    norm_completion = curr / total if total > 0 else 0.0
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)
    return (OLD_W_AGING * norm_aging) + (OLD_W_COMP * norm_completion) + (OLD_W_RSSI * norm_rssi)

def get_new_score(rssi, total, curr, wait_time):
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    rssi_term = math.tanh(norm_rssi)
    norm_completion = curr / total if total > 0 else 0.0
    exp_term = math.exp(W2_COMP * norm_completion)
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)
    return (W1_RSSI * rssi_term) + exp_term + (W3_AGING * norm_aging)

# 테스트 케이스 시나리오 정의
test_cases = {
    "Node_A (신호 양호, 수집 시작 단계)": {"rssi": -45, "total": 100, "curr": 5, "wait_time": 10.0},
    "Node_B (신호 보통, 수집 마감 임박)": {"rssi": -70, "total": 100, "curr": 90, "wait_time": 15.0},
    "Node_C (신호 약함, 기아 상태 직전)": {"rssi": -80, "total": 100, "curr": 20, "wait_time": 55.0},
}

print("=== [스케줄링 공식 비교 검증 시뮬레이션] ===")
for name, data in test_cases.items():
    old_s = get_old_score(data["rssi"], data["total"], data["curr"], data["wait_time"])
    new_s = get_new_score(data["rssi"], data["total"], data["curr"], data["wait_time"])
    print(f"\n[{name}]")
    print(f"  - 환경: RSSI={data['rssi']}dBm, 진척률={data['curr']}/{data['total']}, 대기={data['wait_time']}초")
    print(f"  - 기존 선형 공식 점수: {old_s:.4f}")
    print(f"  - 신규 비선형 공식 점수: {new_s:.4f}")

# 랭킹 비교
old_ranked = sorted(test_cases.keys(), key=lambda k: get_old_score(test_cases[k]["rssi"], test_cases[k]["total"], test_cases[k]["curr"], test_cases[k]["wait_time"]), reverse=True)
new_ranked = sorted(test_cases.keys(), key=lambda k: get_new_score(test_cases[k]["rssi"], test_cases[k]["total"], test_cases[k]["curr"], test_cases[k]["wait_time"]), reverse=True)

print("\n=== [선택 우선순위 결과 비교] ===")
print("기존 선형 공식 랭킹:", [n.split(" ")[0] for n in old_ranked])
print("신규 비선형 공식 랭킹:", [n.split(" ")[0] for n in new_ranked])
```

---

## 5. 진행 절차 및 이행 계획 (Implementation Steps)

구현의 안정적인 반영을 위한 최종 작업 로드맵입니다.

### [Phase 1] 검증 스크립트 작성 및 사전 분석
1. `scratch/test_nonlinear_scheduling.py` 시뮬레이터 파일을 생성합니다.
2. 로컬에서 실행하여 신규 공식 적용 시 시나리오별 우선순위의 변화 양상(예: 거의 완료된 노드가 먼저 끝나는지 여부)을 사전에 확인합니다.

### [Phase 2] 드론 마스터 실코드 적용
1. `server_udp/pi/drone_master.py` 파일의 상단에 `import math`가 정의되어 있는지 확인하고 없으면 추가합니다.
2. 최적화된 비선형 가중치($W_1, W_2, W_3$)와 매핑 상수를 정의합니다.
3. `calculate_score` 함수 내부 로직을 설계안에 맞춰 정밀하게 교체합니다.

### [Phase 3] 실장 테스트 및 디버그
1. 가상 노드 혹은 실제 노드(ESP32-CAM) 여러 대를 활성화한 상태에서 드론 마스터를 구동합니다.
2. 마스터의 콘솔 로그에 나타나는 스케줄링 흐름을 분석하여, 특정 노드의 데이터가 거의 다 받아졌을 때 신속히 권한을 연속 획득(Exponential Boost)하여 완료 처리하는지 실시간 추적합니다.
3. RSSI 급락으로 인한 `REVOKE` 상황 발생 시, Aging 점수가 비선형적 스케줄링 갱신 시 주기에 맞게 기아 현상 없이 공평한 순회를 보장하는지 확인합니다.

### [Phase 4] 문서화 최종 갱신
1. `docs/scheduling_policy.md` 문서 내 "3. 타겟 선택 정책" 장의 점수 공식을 비선형 공식과 $v4.0$ 최적 가중치로 변경합니다.
2. 변경 이력을 문서 말단에 히스토리로 기록하여 개발 정합성을 완료합니다.

---

## 6. 단계별 이행 체크리스트 (Implementation Checklist)

- [x] `scratch/test_nonlinear_scheduling.py` 시뮬레이터 스크립트 작성
- [x] 시뮬레이터를 통한 비선형 랭킹 알고리즘 동작성 사전 검증
- [x] `drone_master.py` 백업본 생성 및 소스 코드(calculate_score) 수정
- [ ] 노드 다중 구동 환경에서 기아(Starvation) 방지 및 전송 병목 완화 확인
- [x] `docs/scheduling_policy.md` 수식 및 파라미터 최적화 내용 문서 업데이트

