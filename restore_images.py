import os
import shutil

def restore_images(source_dir, dest_dir):
    """sent 폴더의 이미지를 다시 상위 이미지 폴더로 복구"""
    if not os.path.exists(source_dir):
        print(f"[Skip] 폴더가 존재하지 않음: {source_dir}")
        return

    files = [f for f in os.listdir(source_dir) if f.lower().endswith('.jpg')]
    if not files:
        print(f"[Info] 복구할 이미지가 없음: {source_dir}")
        return

    print(f"--- {source_dir} -> {dest_dir} 복구 시작 ---")
    for f in files:
        src_path = os.path.join(source_dir, f)
        dst_path = os.path.join(dest_dir, f)
        
        # 목적지에 동일 파일명이 있으면 이름 변경
        if os.path.exists(dst_path):
            dst_path = os.path.join(dest_dir, f"restored_{f}")
            
        try:
            shutil.move(src_path, dst_path)
            print(f" [OK] {f} 이동 완료")
        except Exception as e:
            print(f" [Error] {f} 이동 실패: {e}")

if __name__ == "__main__":
    # 1. ESP 노드 복구
    restore_images("server_udp/esp/images/sent", "server_udp/esp/images")
    
    # 2. ESP1 노드 복구
    restore_images("server_udp/esp1/images/sent", "server_udp/esp1/images")
    
    print("\n--- 모든 이미지 복구 작업이 완료되었습니다. ---")
