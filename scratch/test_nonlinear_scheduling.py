import math
import time

# --- [상수 및 가중치 정의] ---
# 1. 기존 선형 공식 가중치 (v3.7)
OLD_W_RSSI = 0.2
OLD_W_COMP = 0.3
OLD_W_AGING = 0.5
AGING_THRESHOLD = 60.0

# 2. 신규 비선형 공식 가중치 (v4.0)
W1_RSSI = 0.26      # RSSI (tanh)
W2_COMP = 0.48      # Completion (exp)
W3_AGING = 0.26     # Aging (선형)

def calculate_old_score(rssi, total, curr, wait_time):
    """기존 선형 스케줄링 공식"""
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    norm_completion = curr / total if total > 0 else 0.0
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)
    
    score = (OLD_W_RSSI * norm_rssi) + (OLD_W_COMP * norm_completion) + (OLD_W_AGING * norm_aging)
    
    details = {
        "norm_rssi": norm_rssi,
        "rssi_term": norm_rssi,
        "norm_completion": norm_completion,
        "comp_term": norm_completion,
        "norm_aging": norm_aging,
        "aging_term": norm_aging,
        "score": score
    }
    return score, details

def calculate_new_score(rssi, total, curr, wait_time):
    """신규 비선형 스케줄링 공식"""
    # 1. RSSI Term (tanh)
    norm_rssi = max(0.0, min(1.0, (rssi + 100) / 70))
    rssi_term = math.tanh(norm_rssi)
    
    # 2. Completion Term (exp)
    norm_completion = curr / total if total > 0 else 0.0
    exp_term = math.exp(W2_COMP * norm_completion)
    
    # 3. Aging Term (Linear)
    norm_aging = min(1.0, wait_time / AGING_THRESHOLD)
    
    score = (W1_RSSI * rssi_term) + exp_term + (W3_AGING * norm_aging)
    
    details = {
        "norm_rssi": norm_rssi,
        "rssi_term": rssi_term,
        "norm_completion": norm_completion,
        "comp_term": exp_term,
        "norm_aging": norm_aging,
        "aging_term": norm_aging,
        "score": score
    }
    return score, details

# --- [시나리오 테스트 데이터 정의] ---
# 다양한 상황에서의 노드들의 상태 정의
scenarios = {
    "Node_A (최적 신호, 수집 시작)": {
        "rssi": -45,          # 우수한 신호
        "total": 100,
        "curr": 5,            # 완료도 낮음 (5%)
        "wait_time": 10.0     # 미접촉 시간 짧음
    },
    "Node_B (보통 신호, 수집 완료 임박)": {
        "rssi": -70,          # 일반적인 신호
        "total": 100,
        "curr": 90,           # 완료도 높음 (90%)
        "wait_time": 15.0     # 미접촉 시간 보통
    },
    "Node_C (한계 신호, 장기 미접촉 기아 상태)": {
        "rssi": -85,          # 한계 신호
        "total": 100,
        "curr": 20,           # 완료도 낮음 (20%)
        "wait_time": 58.0     # 기아 한계 직전 (Aging 임계에 가까움)
    },
    "Node_D (최악 신호, 수집 중단 대상)": {
        "rssi": -92,          # 통신 불가 수준 (Preemptive Revoke 영역)
        "total": 100,
        "curr": 50,
        "wait_time": 12.0
    }
}

def run_simulation():
    print("=" * 80)
    print("       🛰️  [스케줄링 공식 정밀화 및 가중치 최적화 사전 검증 시뮬레이터] v4.0")
    print("=" * 80)
    print(f" * 기존 선형 가중치: RSSI={OLD_W_RSSI}, Completion={OLD_W_COMP}, Aging={OLD_W_AGING}")
    print(f" * 신규 비선형 가중치: RSSI={W1_RSSI} (tanh 적용), Completion={W2_COMP} (exp 적용), Aging={W3_AGING} (선형)")
    print(f" * Aging Threshold: {AGING_THRESHOLD}s")
    print("=" * 80)
    
    old_rankings = []
    new_rankings = []
    
    for name, data in scenarios.items():
        rssi, total, curr, wait_time = data["rssi"], data["total"], data["curr"], data["wait_time"]
        
        old_score, old_det = calculate_old_score(rssi, total, curr, wait_time)
        new_score, new_det = calculate_new_score(rssi, total, curr, wait_time)
        
        old_rankings.append((name, old_score, old_det))
        new_rankings.append((name, new_score, new_det))
        
        print(f"\n📌 {name}")
        print(f"   [상태값] RSSI: {rssi:3d} dBm | 수집률: {curr/total*100:5.1f}% ({curr}/{total} 청크) | 미접촉 대기: {wait_time:4.1f}초")
        print(f"   -------------------------------------------------------------------------")
        print(f"   [기존 공식] Score: {old_score:6.4f}")
        print(f"               (RSSI 항: {old_det['rssi_term']:5.3f} | Comp 항: {old_det['comp_term']:5.3f} | Aging 항: {old_det['aging_term']:5.3f})")
        print(f"   [신규 공식] Score: {new_score:6.4f}")
        print(f"               (RSSI 항: {new_det['rssi_term']:5.3f} | Exp 항: {new_det['comp_term']:5.3f} | Aging 항: {new_det['aging_term']:5.3f})")
        print(f"   [점수 편차] New - Old = {new_score - old_score:+.4f}")
        
    # 점수 기준으로 정렬
    old_rankings.sort(key=lambda x: x[1], reverse=True)
    new_rankings.sort(key=lambda x: x[1], reverse=True)
    
    print("\n" + "=" * 80)
    print(" 🏆 [스케줄링 알고리즘별 타겟 선택 순위 비교]")
    print("=" * 80)
    print(" 순위 | 기존 선형 공식 (v3.7)                   | 신규 비선형 공식 (v4.0 최적화)")
    print(" ----|-----------------------------------------|-----------------------------------------")
    for i in range(len(scenarios)):
        old_name, old_s, _ = old_rankings[i]
        new_name, new_s, _ = new_rankings[i]
        
        old_lbl = old_name.split(" ")[0]
        new_lbl = new_name.split(" ")[0]
        
        print(f"  {i+1}위 | {old_lbl:<10} (Score: {old_s:.4f})              | {new_lbl:<10} (Score: {new_s:.4f})")
    
    print("=" * 80)
    print("\n💡 [사전 분석 코멘트]")
    
    # 순위 변경점 분석
    old_top = old_rankings[0][0].split(" ")[0]
    new_top = new_rankings[0][0].split(" ")[0]
    
    print(f" 1. 기존 선형 알고리즘 최우선 선택: {old_top}")
    print(f" 2. 신규 비선형 알고리즘 최우선 선택: {new_top}")
    
    print("\n[비선형 공식의 주요 수치적 특성]")
    # Node_B (완료 임박)의 점수 분석
    node_b_old = [r for r in old_rankings if "Node_B" in r[0]][0][1]
    node_b_new = [r for r in new_rankings if "Node_B" in r[0]][0][1]
    print(f" - 완료 임박 노드(Node_B)의 지수 함수(Exp) 변환 효과:")
    print(f"   * 기존 완료도 점수 기여도: {OLD_W_COMP * 0.9:.3f}")
    print(f"   * 신규 지수항 점수 기여도 (exp(0.48 * 0.9)): {math.exp(0.48 * 0.9):.4f} (완료율에 따른 지수적 가속 적용)")
    print(f" - 신호 약함 노드(Node_C)의 tanh 변환 효과:")
    # tanh vs linear RSSI 비교
    node_c_new_det = [r for r in new_rankings if "Node_C" in r[0]][0][2]
    print(f"   * 정규화된 RSSI: {node_c_new_det['norm_rssi']:.4f} -> tanh(NormRSSI): {node_c_new_det['rssi_term']:.4f}")
    print(f"     (신호 품질이 급격히 나쁜 구간인 -85dBm 부근에서 tanh를 통해 완만한 하강 곡선 포화를 유도함)")
    print("=" * 80)

if __name__ == "__main__":
    run_simulation()
