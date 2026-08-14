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
# PHẦN 1: CẤU HÌNH HỆ THỐNG & KIỂM TRA QUYỀN ROOT
# ==============================================================================
PORT = 9999
MAX_NO_PING = 180
LAUNCH_INTERVAL = 15
RESTART_DELAY = 3
CONFIG_FILE = "accounts.json"
COOKIE_FILE = "cookie.txt"

DEFAULT_ACCOUNTS = [
    {
        "username": "alt_bluehive001",
        "package": "free.nokaA",
        "place_id": 1537690962,
        "vip_link": "",
        "enabled": True,
    },
    {
        "username": "trieutantai",
        "package": "free.nokaB",
        "place_id": 1537690962,
        "vip_link": "",
        "enabled": True,
    },
    {
        "username": "taitantrieu_111",
        "package": "free.nokaC",
        "place_id": 1537690962,
        "vip_link": "",
        "enabled": True,
    },
    {
        "username": "taitantrieu_121",
        "package": "free.nokaD",
        "place_id": 1537690962,
        "vip_link": "",
        "enabled": True,
    },
    {
        "username": "taitantrieu_122",
        "package": "free.nokaE",
        "place_id": 1537690962,
        "vip_link": "",
        "enabled": True,
    },
]


def check_root_permission():
    """1. Kiểm tra quyền ROOT ngay từ đầu (su -c id)"""
    try:
        res = subprocess.run(
            ["su", "-c", "id"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode != 0 or "uid=0(root)" not in res.stdout:
            print("[X] LỖI: Thiết bị chưa được ROOT hoặc chưa cấp quyền SuperUser (SU) cho Termux!")
            print("[!] Vui lòng mở ứng dụng Magisk / KernelSU / APatch để cấp quyền ROOT.")
            sys.exit(1)
    except Exception as e:
        print(f"[X] Không thể kiểm tra quyền ROOT: {e}")
        sys.exit(1)


def read_file_safely(filepath):
    """2. Xử lý ngoại lệ khi đọc file & 3. Kiểm tra file rỗng"""
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                print(f"[!] CẢNH BÁO: File [{filepath}] đang bị TRỐNG!")
                return ""
            return content.strip()
    except OSError as e:
        print(f"[X] Lỗi hệ thống khi đọc file [{filepath}]: {e}")
        return None


def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        save_accounts(DEFAULT_ACCOUNTS)
        return DEFAULT_ACCOUNTS

    content = read_file_safely(CONFIG_FILE)
    if not content:
        return DEFAULT_ACCOUNTS

    try:
        data = json.loads(content)
        for acc in data:
            if "enabled" not in acc:
                acc["enabled"] = True
            if "username" not in acc:
                acc["username"] = f"Player_{acc.get('package', 'Unknown')}"
        return data
    except Exception:
        return DEFAULT_ACCOUNTS


def save_accounts(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as e:
        print(f"[X] Lỗi khi lưu file cấu hình: {e}")


ACCOUNTS = load_accounts()
last_ping = {acc["username"]: 0 for acc in ACCOUNTS}
has_pinged = {acc["username"]: False for acc in ACCOUNTS}

ping_lock = threading.Lock()
last_idle = 0
last_total = 0


# ==============================================================================
# HÀM XỬ LÝ MÀN HÌNH & HỆ THỐNG
# ==============================================================================
def safe_print(text=""):
    sys.stdout.write(text + "\r\n")
    sys.stdout.flush()


def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def get_installed_roblox_packages():
    """Đọc động tất cả các Package clone Roblox trên máy thay vì hardcode"""
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


def get_cpu_percentage():
    global last_idle, last_total
    try:
        with open("/proc/stat", "r") as f:
            fields = [float(column) for column in f.readline().strip().split()[1:]]

        idle_time = fields[3] + fields[4]
        total_time = sum(fields)

        idle_delta = idle_time - last_idle
        total_delta = total_time - last_total

        last_idle = idle_time
        last_total = total_time

        if total_delta == 0:
            return "0%"

        cpu_pct = 100.0 * (1.0 - idle_delta / total_delta)
        return f"{cpu_pct:.1f}%"
    except Exception:
        return "N/A"


def get_ram_info():
    try:
        mem_total = 0
        mem_avail = 0
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if "MemTotal:" in line:
                    mem_total = int(line.split()[1])
                elif "MemAvailable:" in line:
                    mem_avail = int(line.split()[1])
                if mem_total and mem_avail:
                    break

        if mem_total > 0:
            total_gb = mem_total / 1024 / 1024
            avail_gb = mem_avail / 1024 / 1024
            used_gb = total_gb - avail_gb
            return f"{used_gb:.1f}GB / {total_gb:.1f}GB ({avail_gb:.1f}GB Có sẵn)"
    except Exception:
        pass
    return "N/A"


# ==============================================================================
# HÀM TẠO THAO TÁC FILE TẠM AN TOÀN VỚI TRY/FINALLY
# ==============================================================================
def execute_temp_script(pkg, cmd_str):
    """4. Dọn file tạm an toàn bằng try...finally"""
    temp_file = f"/data/local/tmp/temp_script_{pkg}.sh"
    try:
        # Ghi script tạm bằng quyền root
        write_cmd = f"echo '{cmd_str}' > {temp_file} && chmod 777 {temp_file}"
        subprocess.run(["su", "-c", write_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Thực thi file tạm
        subprocess.run(["su", "-c", f"sh {temp_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        safe_print(f"[X] Lỗi thực thi file tạm cho {pkg}: {e}")
    finally:
        # Đảm bảo LUÔN LUÔN xóa file tạm cho dù có xảy ra ngoại lệ
        subprocess.run(["su", "-c", f"rm -f {temp_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ==============================================================================
# BÓC TÁCH LINK & REJOIN
# ==============================================================================
def parse_vip_link(vip_link, place_id):
    if not vip_link or not vip_link.strip():
        return f"roblox://placeId={place_id}"

    vip_link = vip_link.strip()

    if "privateServerLinkCode=" in vip_link:
        parsed = urllib.parse.urlparse(vip_link)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("privateServerLinkCode", [""])[0]
        if code:
            return f"roblox://placeId={place_id}&linkCode={code}"

    elif "/share" in vip_link and "code=" in vip_link:
        parsed = urllib.parse.urlparse(vip_link)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        if code:
            return f"roblox://placeId={place_id}&linkCode={code}"

    elif vip_link.startswith("roblox://"):
        return vip_link

    elif len(vip_link) == 36 and "-" in vip_link:
        return f"roblox://placeId={place_id}&gameInstanceId={vip_link}"

    return f"roblox://placeId={place_id}"


def restart_account(acc):
    username = acc["username"]
    pkg = acc["package"]  # Đọc linh hoạt Package từ cấu hình người dùng chọn
    place_id = acc.get("place_id", 1537690962)
    vip_link = acc.get("vip_link", "")

    now_str = time.strftime("%H:%M:%S")
    safe_print(f"[{now_str}] Đang khởi chạy ({place_id}): {username} ({pkg})...")

    kill_cmd = f'ps -A | grep -w "{pkg}" | awk "{{print \\$2}}" | xargs kill -9'
    execute_temp_script(pkg, kill_cmd)

    time.sleep(RESTART_DELAY)

    deep_link = parse_vip_link(vip_link, place_id)

    result = subprocess.run(
        [
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            deep_link,
            "-p",
            pkg,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        subprocess.run(
            [
                "am",
                "start",
                "-n",
                f"{pkg}/com.roblox.client.startup.ActivitySplash",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                deep_link,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    with ping_lock:
        last_ping[username] = time.time()
        has_pinged[username] = False


# ==============================================================================
# SERVER LẮNG NGHE PING
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
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception:
            pass

    def log_message(self, format, *args):
        return


# ==============================================================================
# DANH SÁCH MENU DÀNH CHO NGUỜI DÙNG
# ==============================================================================
def toggle_clients_menu():
    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("      DANH SÁCH CLIENT & TRẠNG THÁI       ")
        safe_print("==========================================")

        for i, acc in enumerate(ACCOUNTS, 1):
            status_str = "[ON] BẬT " if acc.get("enabled", True) else "[OFF] TẮT"
            safe_print(
                f" [{i}] Trạng thái: {status_str} | User: {acc['username']:<15} | App: {acc['package']}"
            )

        safe_print("------------------------------------------")
        safe_print(" LỰA CHỌN THAO TÁC:")
        safe_print(" [1-5] Nhập số thứ tự để Bật/Tắt Client tương ứng")
        safe_print(" [88]  BẬT TẤT CẢ CLIENT")
        safe_print(" [99]  TẮT TẤT CẢ CLIENT")
        safe_print(" [0]   Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip().lower()

        if choice == "0":
            break

        elif choice == "88":
            for acc in ACCOUNTS:
                acc["enabled"] = True
            save_accounts(ACCOUNTS)
            safe_print("[+] Đã BẬT tất cả các Client!")
            time.sleep(1)

        elif choice == "99":
            for acc in ACCOUNTS:
                acc["enabled"] = False
            save_accounts(ACCOUNTS)
            safe_print("[+] Đã TẮT tất cả các Client!")
            time.sleep(1)

        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ACCOUNTS):
                target_acc = ACCOUNTS[idx]
                target_acc["enabled"] = not target_acc.get("enabled", True)
                save_accounts(ACCOUNTS)


def scan_installed_packages_menu():
    clear_screen()
    safe_print("==========================================")
    safe_print("    ĐANG QUÉT PACKAGE TRÊN MÁY (ROOT)...  ")
    safe_print("==========================================")

    installed_pkgs = get_installed_roblox_packages()
    enabled_accounts = [acc for acc in ACCOUNTS if acc.get("enabled", True)]

    clear_screen()
    safe_print("==========================================")
    safe_print("    DANH SÁCH PACKAGE ĐANG BẬT (ON)       ")
    safe_print("==========================================")

    if not enabled_accounts:
        safe_print("[!] Hiện tại KHÔNG CÓ Client nào đang BẬT (ON)!")
        safe_print("[!] Hãy vào Mục [2] để BẬT Client trước.")
    else:
        count = 0
        for acc in enabled_accounts:
            count += 1
            pkg = acc["package"]
            user = acc["username"]

            installed_status = (
                "(Đã cài trên máy)"
                if pkg in installed_pkgs
                else "(Chưa cài/Không tìm thấy)"
            )

            safe_print(
                f" [{count}] Package: {pkg:<15} | Player: {user:<15} {installed_status}"
            )

    safe_print("------------------------------------------")
    input("Nhấn Enter để quay lại Menu chính...")


def apply_game_config(target_accounts, title="CLIENT"):
    while True:
        clear_screen()
        safe_print(f"=== CẤU HÌNH CHO: {title} ===")
        safe_print("------------------------------------------")
        safe_print("Chọn chế độ nhập:")
        safe_print(" [0] Quay lại menu trước")
        safe_print(" [1] Nhập PlaceID / Link Game / JobID thủ công")
        safe_print(" [2] Bee Swarm Simulator (PlaceID: 1537690962)")
        safe_print(" [3] King Legacy (PlaceID: 4520749081)")
        safe_print("==========================================")

        mode = input("Lựa chọn của bạn (0-3): ").strip()

        if mode == "0":
            break

        elif mode == "1":
            inp = input("\nNhập PlaceID HOẶC Link Server/JobID: ").strip()
            if inp.isdigit():
                p_id = int(inp)
                for acc in target_accounts:
                    acc["place_id"] = p_id
                safe_print(f"[+] Đã cập nhật Place ID thành [{p_id}] cho {title}!")
            else:
                for acc in target_accounts:
                    acc["vip_link"] = inp
                safe_print(f"[+] Đã cập nhật Link Server / JobID cho {title}!")

            save_accounts(ACCOUNTS)
            time.sleep(1.2)
            break

        elif mode == "2":
            for acc in target_accounts:
                acc["place_id"] = 1537690962
            save_accounts(ACCOUNTS)
            safe_print(f"[+] Đã đổi Game thành [Bee Swarm] cho {title}!")
            time.sleep(1.2)
            break

        elif mode == "3":
            for acc in target_accounts:
                acc["place_id"] = 4520749081
            save_accounts(ACCOUNTS)
            safe_print(f"[+] Đã đổi Game thành [King Legacy] cho {title}!")
            time.sleep(1.2)
            break


def config_server_menu():
    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("     CẤU HÌNH GAME & SERVER CLIENT        ")
        safe_print("==========================================")

        for i, acc in enumerate(ACCOUNTS, 1):
            link_display = acc.get("vip_link", "").strip()
            p_id = acc.get("place_id", 1537690962)

            if not link_display:
                link_display = "[Public Server]"
            elif len(link_display) > 22:
                link_display = link_display[:19] + "..."

            safe_print(
                f" [{i}] {acc['username']:<15} | PlaceID: {p_id} | {link_display}"
            )

        safe_print("------------------------------------------")
        safe_print(" LỰA CHỌN CÀI ĐẶT:")
        safe_print(" [99] Cài đặt chung cho TẤT CẢ CLIENT (ALL)")
        safe_print(" [1-5] Chọn riêng từng Client để cài đặt")
        safe_print(" [0] Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break

        elif choice == "99":
            apply_game_config(ACCOUNTS, title="TẤT CẢ CLIENT (ALL)")

        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ACCOUNTS):
                target_acc = ACCOUNTS[idx]
                apply_game_config([target_acc], title=f"CLIENT {target_acc['username']}")


def set_username_menu():
    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("       ĐỔI TÊN PLAYER (ROBLOX USERNAME)    ")
        safe_print("==========================================")

        for i, acc in enumerate(ACCOUNTS, 1):
            safe_print(
                f" [{i}] {acc['username']:<15} | App Package: {acc['package']}"
            )

        safe_print("------------------------------------------")
        safe_print(" [1-5] Chọn số tương ứng để đổi tên Player")
        safe_print(" [0] Quay lại Menu chính")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn: ").strip()

        if choice == "0":
            break

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ACCOUNTS):
                acc = ACCOUNTS[idx]
                old_name = acc["username"]
                new_name = input(
                    f"\nNhập tên Username Roblox mới cho [{acc['package']}] (Tên cũ: {old_name}): "
                ).strip()

                if new_name:
                    with ping_lock:
                        if old_name in last_ping:
                            last_ping[new_name] = last_ping.pop(old_name)
                            has_pinged[new_name] = has_pinged.pop(old_name)
                        else:
                            last_ping[new_name] = 0
                            has_pinged[new_name] = False

                    acc["username"] = new_name
                    save_accounts(ACCOUNTS)
                    safe_print(f"[+] Đã đổi tên thành công thành: [{new_name}]")
                    time.sleep(1.2)


def run_manager():
    subprocess.run(["stty", "sane"], stderr=subprocess.DEVNULL)

    initial_active = [acc for acc in ACCOUNTS if acc.get("enabled", True)]
    if not initial_active:
        safe_print("\n[!] Không có Client nào đang BẬT (ON)!")
        safe_print("[!] Vui lòng vào Mục [2] để bật ít nhất 1 Client.")
        input("\nNhấn Enter để quay lại Menu...")
        return

    server = MultithreadedTCPServer(("127.0.0.1", PORT), PingHandler)
    server.timeout = 1.0

    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    safe_print("\n[+] Đang khởi chạy danh sách app (Những Client [ON])...")
    for acc in initial_active:
        restart_account(acc)
        safe_print(f" -> Đợi {LAUNCH_INTERVAL}s trước khi mở app tiếp theo...")
        time.sleep(LAUNCH_INTERVAL)

    get_cpu_percentage()

    try:
        while True:
            current_time = time.time()
            clear_screen()

            cpu_usage = get_cpu_percentage()
            ram_usage = get_ram_info()
            now_str = time.strftime("%H:%M:%S")

            safe_print("==========================================")
            safe_print(f" TERMUX REJOIN MANAGER (Port {PORT})")
            safe_print(
                f" Time: {now_str} | Limit: {MAX_NO_PING}s | Delay:"
                f" {LAUNCH_INTERVAL}s"
            )
            safe_print(f" CPU Mức tải : {cpu_usage}")
            safe_print(f" RAM Sử dụng : {ram_usage}")
            safe_print("==========================================")

            active_now = [acc for acc in ACCOUNTS if acc.get("enabled", True)]

            for acc in ACCOUNTS:
                user = acc["username"]
                pkg = acc["package"]

                if not acc.get("enabled", True):
                    safe_print(f" {user:<15} | {pkg:<10} | [DISABLED / TẮT]")
                    continue

                with ping_lock:
                    user_last_ping = last_ping.get(user, 0)
                    user_has_pinged = has_pinged.get(user, False)

                diff = int(current_time - user_last_ping)

                if user_has_pinged:
                    if diff <= MAX_NO_PING:
                        status_str = f"ONLINE ({diff}s ago)"
                    else:
                        status_str = f"TIMEOUT ({diff}s ago)"
                else:
                    status_str = f"STARTING... ({diff}s/{MAX_NO_PING}s)"

                safe_print(f" {user:<15} | {pkg:<10} | {status_str}")

            safe_print("------------------------------------------")

            for acc in active_now:
                user = acc["username"]
                with ping_lock:
                    user_last_ping = last_ping.get(user, 0)

                diff = current_time - user_last_ping

                if diff > MAX_NO_PING:
                    threading.Thread(
                        target=restart_account, args=(acc,), daemon=True
                    ).start()
                    with ping_lock:
                        last_ping[user] = current_time

            time.sleep(3)

    except KeyboardInterrupt:
        safe_print("\n[!] Đang dừng script theo yêu cầu người dùng...")
    except Exception as e:
        safe_print(f"\n[!] Phát hiện lỗi ngoại lệ: {e}")
    finally:
        safe_print("[+] Đang dọn dẹp tài nguyên và tắt Server...")
        server.shutdown()
        server.server_close()
        sys.exit(0)


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    # 1. Kiểm tra quyền ROOT ngay khi vừa chạy Script
    check_root_permission()

    while True:
        clear_screen()
        safe_print("==========================================")
        safe_print("      TERMUX REJOIN AUTOMATION MENU       ")
        safe_print("==========================================")
        safe_print(" [1] Cài đặt Game & Link Server Client")
        safe_print(" [2] Liệt kê Client & Bật / Tắt (ON/OFF)")
        safe_print(" [3] Đổi Tên Player (Roblox Username)")
        safe_print(" [4] Bắt đầu chạy kịch bản Rejoin")
        safe_print(" [5] Quản lý Package (Chỉ hiện các Package ON)")
        safe_print(" [0] Thoát")
        safe_print("==========================================")

        choice = input("Nhập lựa chọn của bạn (0-5): ").strip()

        if choice == "1":
            config_server_menu()
        elif choice == "2":
            toggle_clients_menu()
        elif choice == "3":
            set_username_menu()
        elif choice == "4":
            clear_screen()
            run_manager()
            break
        elif choice == "5":
            scan_installed_packages_menu()
        elif choice == "0":
            safe_print("Đã thoát chương trình.")
            sys.exit(0)
