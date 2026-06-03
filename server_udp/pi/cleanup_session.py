#!/usr/bin/env python3
import os
import sqlite3
import glob

# 설정 경로
DB_PATH = "../../drone_system.db"  # server_udp/pi/ 디렉토리 기준 상위 경로
STORAGE_DIR = "collected_images"

def main():
    print("=== [System Cleanup] 오염 데이터 및 세션 초기화 시작 ===")
    
    # 1. SQLite 데이터베이스 정리
    # 마스터 실행 디렉토리에 맞춰 상대 경로 설정 조정
    possible_db_paths = [DB_PATH, "drone_system.db", "../drone_system.db"]
    db_found = False
    
    for db_p in possible_db_paths:
        if os.path.exists(db_p):
            try:
                conn = sqlite3.connect(db_p)
                cursor = conn.cursor()
                
                # S03 노드(문제가 발생한 노드)의 세션 삭제
                cursor.execute("DELETE FROM image_sessions WHERE s_id = 'S03';")
                conn.commit()
                
                # 다른 수집 중인 세션이 있다면 초기 상태(PAUSED)로 정합성 보정
                cursor.execute("UPDATE image_sessions SET received_count = 0, status = 'PAUSED';")
                conn.commit()
                
                print(f"[DB Success] '{db_p}' 내부의 오염된 전송 세션 정보 초기화 완료.")
                conn.close()
                db_found = True
                break
            except Exception as e:
                print(f"[DB Error] 데이터베이스 청소 실패 ({db_p}): {e}")
                
    if not db_found:
        print("[DB Skip] 초기화할 데이터베이스 파일(drone_system.db)이 감지되지 않았습니다.")

    # 2. 임시 꼬임 파일(*.tmp) 물리적 제거
    # 저장 폴더 탐색 (마스터 구동 디렉토리 기준)
    possible_dirs = [STORAGE_DIR, os.path.join("..", STORAGE_DIR), os.path.join("..", "..", STORAGE_DIR)]
    dir_found = False
    
    for s_dir in possible_dirs:
        if os.path.exists(s_dir):
            tmp_pattern = os.path.join(s_dir, "*S03*.tmp")
            tmp_files = glob.glob(tmp_pattern)
            
            if tmp_files:
                for fpath in tmp_files:
                    try:
                        os.remove(fpath)
                        print(f"[File Success] 손상된 임시 파일 제거 완료: {os.path.basename(fpath)}")
                    except Exception as e:
                        print(f"[File Error] 파일 제거 실패 ({fpath}): {e}")
            else:
                print(f"[File Skip] {s_dir} 디렉토리에 제거할 손상된 임시 파일(*.tmp)이 없습니다.")
            dir_found = True
            break
            
    if not dir_found:
        print("[File Skip] 이미지 저장 디렉토리(collected_images)가 아직 감지되지 않았습니다.")

    print("=== [System Cleanup] 클리닝 완료. 신규 전송 테스트 준비 완료. ===")

if __name__ == "__main__":
    main()
