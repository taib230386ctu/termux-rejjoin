import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

# ==============================================================================
# PHẦN 1: CẤU HÌNH HỆ THỐNG & KIỂM TRA ROOT
# ==============================================================================
PORT = 9999
MAX_NO_PING = 90
LAUNCH_INTERVAL = 15
RESTART_DELAY = 3
CONFIG_FILE = "accounts.json"

ping_lock = threading.Lock()
last_idle = 0
last_total = 0


def check_root_permission():
    """Kiểm tra quyền ROOT ngay từ đầu"""
    try:
        res = subprocess.run(
            ["su", "-c", "id"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode != 0 or "uid=0(root)" not in res.stdout:
            print("[X] LỖI: Thiết bị chưa được ROOT hoặc chưa cấp quyền SU cho Termux!")
            sys.exit(1)
    except Exception as e:
        print(f"[X] Không thể kiểm tra quyền ROOT: {e}")
        sys.exit(1)


def safe_print(text=""):
    sys.stdout.write(text + "\r\n")
    sys.stdout.flush()


def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


# ==============================================================================
# HÀM QUÉT PACKAGE TRÊN MÁY VÀ ĐỒNG BỘ DỮ LIỆU
# ==============================================================================
def get_installed_roblox_packages():
    """Quét thực tế các ứng dụng Roblox/Noka/Clone đang cài trên máy"""
    try:
        res = subprocess.run(
            ["su", "-c", "pm list packages"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            pkgs = []
            for line in lines:
                if line.startswith("package:"):
                    p_name = line.replace("package:", "").strip()
                    if any(
                        keyword in p_name.lower()
                        for keyword in ["roblox", "noka", "free."]
                    ):
                        pkgs.append(p_name)
            return sorted(pkgs)
    except Exception:
        pass
    return []


def load_accounts():
    """Tải cấu hình từ file json"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception:
            pass
    return []


def save_accounts(data):
    """Lưu cấu hình ra file json"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as e:
        safe_print(f"[X] Lỗi khi lưu file: {e}")


ACCOUNTS = load_accounts()


def sync_packages():
    """Hàm tự động quét máy và đồng bộ với danh sách ACCOUNTS"""
    global ACCOUNTS
    installed = get_installed_roblox_packages()
    
    # Nếu chưa từng có file json, tự động tạo theo danh sách quét được
    acc_map = {acc["package"]: acc for acc in ACCOUNTS}
    updated_accounts = []

    for pkg in installed:
        if pkg in acc_map:
            updated_accounts.append(acc_map[pkg])
        else:
            # Package mới phát hiện trên máy -> Tạo mặc định
            updated_accounts.append({
                "package": pkg,
                "username": f"Player_{pkg.split('.')[-1]}",
                "place_id": 1537690962,
                "vip_link": "",
                "pkg_enabled": False,  # Mặc định tắt ở Mục 5
                "client_enabled": True # Trạng thái chạy ở Mục 2
            })
    
    ACCOUNTS = updated_accounts
    save_accounts(ACCOUNTS)


# Tải & đồng bộ lúc khởi động
sync_packages()

last_ping = {acc["username"]: 0 for acc in ACCOUNTS}
has_pinged = {acc["username"]: False for acc in ACCOUNTS}


# ==============================================================================
# BÓC TÁCH LINK & KHỞI CHẠY APP
# ==============================================================================
def execute_temp_script(pkg, cmd_str):
    temp_file = f"/data/local/tmp/temp_script_{pkg}.sh"
    try:
        write_cmd = f"echo '{cmd_str}' > {temp_file} && chmod 777 {temp_file}"
        subprocess.run(["su", "-c", write_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["su", "-c", f"sh {temp_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    finally:
        subprocess.run(["su", "-c", f"rm -f {temp_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_vip_link(vip_link, place_id):
    if not vip_link or not vip_link.strip():
        return f"roblox://placeId={place_id}"

    vip_link = vip_link.strip()
    if "privateServerLinkCode=" in vip_link:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(vip_link).query).get("privateServerLinkCode", [""])[0]
        if code: return f"roblox://placeId={place_id}&linkCode={code}"
    elif "/share" in vip_link and "code=" in vip_link:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(vip_link).query).get("code", [""])[0]
        if code: return f"roblox://placeId={place_id}&linkCode={code}"
    elif vip_link.startswith("roblox://"):
        return vip_link
    elif len(vip_link) == 36 and "-" in vip_link:
        return f"roblox://placeId={place_id}&gameInstanceId={vip_link}"

    return f"roblox://placeId={place_id}"


def restart_account(acc):
    username = acc["username"]
    pkg = acc["package"]
    place_id = acc.get("place_id", 1537690962)
    vip_link = acc.get("vip_link", "")

    now_str = time.strftime("%H:%M:%S")
    safe_print(f"[{now_str}] Đang khởi chạy: {username} ({pkg})...")

    kill_cmd = f'ps -A | grep -w "{pkg}" | awk "{{print \\$2}}" | xargs kill -9'
    execute_temp_script(pkg, kill_cmd)

    time.sleep(RESTART_DELAY)
    deep_link = parse_vip_link(vip_link, place_id)

    res = subprocess.run(["am", "start", "-a", "android.intent.action.VIEW", "-d", deep_link, "-p", pkg],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if res.returncode != 0:
        subprocess.run(["am", "start", "-n", f"{pkg}/com.roblox.client.startup.ActivitySplash", "-a", "android.intent.action.VIEW", "-d", deep_link],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with ping_lock:
        last_ping[username] = time.time()
        has_pinged[username] = False


# ==============================================================================
# MỤC 5: QUẢN LÝ PACKAGE TRÊN MÁY (BẬT/TẮT PACKAGE)
# ==============================================================================
def manage_packages_menu():
    sync_packages() # Cập nhật danh sách mới nhất từ máy
    
    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("   QUẢN LÝ PACKAGE ROBLOX CÓ TRÊN MÁY     ")
        safe_print("==========================================")

        if not ACCOUNTS:
            safe_print("[!] Không tìm thấy Package Roblox/Noka nào trên máy!")
        else:
            for i, acc in enumerate(ACCOUNTS, 1):
                status = "[ON]  BẬT" if acc.get("pkg_enabled", False) else "[OFF] TẮT"
                safe_print(f" [{i}] {status} | Package: {acc['package']}")

        safe_print("------------------------------------------")
        safe_print(" [1-N] Nhập số thứ tự để BẬT/TẮT Package tương ứng")
        safe_print(" [88]  BẬT TẤT CẢ PACKAGE")
        safe_print(" [99]  TẮT TẤT CẢ PACKAGE")
        safe_print(" [0]   Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break
        elif choice == "88":
            for acc in ACCOUNTS: acc["pkg_enabled"] = True
            save_accounts(ACCOUNTS)
        elif choice == "99":
            for acc in ACCOUNTS: acc["pkg_enabled"] = False
            save_accounts(ACCOUNTS)
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ACCOUNTS):
                ACCOUNTS[idx]["pkg_enabled"] = not ACCOUNTS[idx].get("pkg_enabled", False)
                save_accounts(ACCOUNTS)


# ==============================================================================
# MỤC 2: QUẢN LÝ CLIENT (CHỈ HIỂN THỊ CÁC PACKAGE ĐÃ ON Ở MỤC 5)
# ==============================================================================
def manage_clients_menu():
    while True:
        # Lọc danh sách: CHỈ lấy các Package đã BẬT [ON] ở Mục 5
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", False)]

        clear_screen()
        safe_print("==========================================")
        safe_print("  DANH SÁCH CLIENT (PACKAGE ĐÃ CHỌN ON)   ")
        safe_print("==========================================")

        if not active_pkgs:
            safe_print("[!] Chưa có Package nào được BẬT ở Mục [5]!")
            safe_print("[!] Vui lòng vào Mục [5] để chọn BẬT Package trước.")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                status = "[RUNNING]" if acc.get("client_enabled", True) else "[STOPPED]"
                safe_print(
                    f" [{i}] {status:<9} | Player: {acc['username']:<15} | App: {acc['package']}"
                )

        safe_print("------------------------------------------")
        safe_print(" [1-N] Nhập số để Bật/Tắt trạng thái chạy của Client")
        safe_print(" [0]   Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                active_pkgs[idx]["client_enabled"] = not active_pkgs[idx].get("client_enabled", True)
                save_accounts(ACCOUNTS)


# ==============================================================================
# MỤC 3: ĐỔI TÊN PLAYER (CHỈ HIỂN THỊ CÁC PACKAGE ĐÃ ON Ở MỤC 5)
# ==============================================================================
def set_username_menu():
    while True:
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", False)]

        clear_screen()
        safe_print("==========================================")
        safe_print("       ĐỔI TÊN PLAYER (ROBLOX USERNAME)    ")
        safe_print("==========================================")

        if not active_pkgs:
            safe_print("[!] Hãy BẬT Package ở Mục [5] trước khi đổi tên Player!")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                safe_print(f" [{i}] User: {acc['username']:<15} | App: {acc['package']}")

        safe_print("------------------------------------------")
        safe_print(" [1-N] Chọn Client để đổi tên Player tương ứng")
        safe_print(" [0]   Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                acc = active_pkgs[idx]
                old_name = acc["username"]
                new_name = input(f"\nNhập Username Roblox mới cho [{acc['package']}] (Cũ: {old_name}): ").strip()
                if new_name:
                    acc["username"] = new_name
                    save_accounts(ACCOUNTS)
                    safe_print(f"[+] Đã đổi tên thành công: [{new_name}]")
                    time.sleep(1)


# ==============================================================================
# MỤC 1: CẤU HÌNH GAME (CHỈ HIỂN THỊ CÁC PACKAGE ĐÃ ON Ở MỤC 5)
# ==============================================================================
def config_server_menu():
    while True:
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", False)]

        clear_screen()
        safe_print("==========================================")
        safe_print("     CẤU HÌNH GAME & SERVER CLIENT        ")
        safe_print("==========================================")

        if not active_pkgs:
            safe_print("[!] Hãy BẬT Package ở Mục [5] trước khi cài đặt Game!")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                link_display = acc.get("vip_link", "").strip() or "[Public Server]"
                if len(link_display) > 20: link_display = link_display[:17] + "..."
                safe_print(f" [{i}] {acc['username']:<15} | PlaceID: {acc.get('place_id', 1537690962)} | {link_display}")

        safe_print("------------------------------------------")
        safe_print(" [99]  Cài đặt cho TẤT CẢ Client đang mở")
        safe_print(" [1-N] Chọn riêng từng Client để cài đặt")
        safe_print(" [0]   Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break
        elif choice == "99" and active_pkgs:
            inp = input("\nNhập PlaceID HOẶC Link Server VIP: ").strip()
            for acc in active_pkgs:
                if inp.isdigit(): acc["place_id"] = int(inp)
                else: acc["vip_link"] = inp
            save_accounts(ACCOUNTS)
            safe_print("[+] Cập nhật thành công!")
            time.sleep(1)
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                target_acc = active_pkgs[idx]
                inp = input(f"\nNhập PlaceID HOẶC Link Server VIP cho [{target_acc['username']}]: ").strip()
                if inp.isdigit(): target_acc["place_id"] = int(inp)
                else: target_acc["vip_link"] = inp
                save_accounts(ACCOUNTS)
                safe_print("[+] Cập nhật thành công!")
                time.sleep(1)


# ==============================================================================
# MỤC 4: BẮT ĐẦU VÒNG LẶP REJOIN AUTOMATION
# ==============================================================================
class MultithreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class PingHandler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(3.0)
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                if "PING_USER:" in body:
                    username = body.split("PING_USER:")[1].strip()
                    with ping_lock:
                        if username in last_ping:
                            last_ping[username] = time.time()
                            has_pinged[username] = True
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception: pass
    def log_message(self, format, *args): return


def run_manager():
    # Chỉ lấy các Client đã BẬT Package ở MỤC 5 VÀ BẬT Chạy ở MỤC 2
    runnable_accounts = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", False) and acc.get("client_enabled", True)]

    if not runnable_accounts:
        safe_print("\n[!] Không có Client nào đủ điều kiện chạy!")
        safe_print("[!] Hãy chắc chắn bạn đã: BẬT Package ở Mục [5] VÀ BẬT Client ở Mục [2].")
        input("\nNhấn Enter để quay lại Menu...")
        return

    server = MultithreadedTCPServer(("127.0.0.1", PORT), PingHandler)
    server.timeout = 1.0
    threading.Thread(target=server.serve_forever, daemon=True).start()

    safe_print("\n[+] Đang khởi chạy danh sách app...")
    for acc in runnable_accounts:
        restart_account(acc)
        time.sleep(LAUNCH_INTERVAL)

    try:
        while True:
            current_time = time.time()
            clear_screen()
            now_str = time.strftime("%H:%M:%S")

            safe_print("==========================================")
            safe_print(f" TERMUX REJOIN MANAGER (Port {PORT}) | {now_str}")
            safe_print("==========================================")

            for acc in runnable_accounts:
                user = acc["username"]
                pkg = acc["package"]
                with ping_lock:
                    u_last_ping = last_ping.get(user, 0)
                    u_has_pinged = has_pinged.get(user, False)

                diff = int(current_time - u_last_ping)
                if u_has_pinged:
                    status_str = f"ONLINE ({diff}s ago)" if diff <= MAX_NO_PING else f"TIMEOUT ({diff}s ago)"
                else:
                    status_str = f"STARTING... ({diff}s/{MAX_NO_PING}s)"

                safe_print(f" {user:<15} | {pkg:<10} | {status_str}")

            safe_print("------------------------------------------")

            for acc in runnable_accounts:
                user = acc["username"]
                with ping_lock:
                    u_last_ping = last_ping.get(user, 0)

                if current_time - u_last_ping > MAX_NO_PING:
                    threading.Thread(target=restart_account, args=(acc,), daemon=True).start()
                    with ping_lock: last_ping[user] = current_time

            time.sleep(3)

    except KeyboardInterrupt:
        safe_print("\n[!] Đã dừng chương trình.")
    finally:
        server.shutdown()
        server.server_close()
        sys.exit(0)


# ==============================================================================
# MENU CHÍNH
# ==============================================================================
if __name__ == "__main__":
    check_root_permission()

    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("      TERMUX REJOIN AUTOMATION MENU       ")
        safe_print("==========================================")
        safe_print(" [1] Cài đặt Game & Link Server Client")
        safe_print(" [2] Quản lý Client (Xem các Package chọn ở Mục 5)")
        safe_print(" [3] Đổi Tên Player (Roblox Username)")
        safe_print(" [4] Bắt đầu chạy kịch bản Rejoin")
        safe_print(" [5] Quản lý Package trên máy (Chọn ON/OFF Package)")
        safe_print(" [0] Thoát")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn (0-5): ").strip()

        if choice == "1": config_server_menu()
        elif choice == "2": manage_clients_menu()
        elif choice == "3": set_username_menu()
        elif choice == "4":
            clear_screen()
            run_manager()
            break
        elif choice == "5": manage_packages_menu()
        elif choice == "0":
            safe_print("Đã thoát chương trình.")
            sys.exit(0)
