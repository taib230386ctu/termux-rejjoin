import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from prompt_toolkit import prompt

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


def clear_screen():
    os.system("clear")


# ==============================================================================
# HÀM ĐO CPU VÀ RAM
# ==============================================================================
def get_system_stats():
    global last_idle, last_total
    cpu_usage = 0.0
    used_gb = 0.0
    total_gb = 0.0
    ram_percentage = 0.0

    try:
        with open("/proc/stat", "r") as f:
            fields = [float(column) for column in f.readline().strip().split()[1:]]
        idle_time = fields[3] + fields[4]
        total_time = sum(fields)

        if last_total != 0:
            total_diff = total_time - last_total
            idle_diff = idle_time - last_idle
            if total_diff > 0:
                cpu_usage = (1.0 - idle_diff / total_diff) * 100.0

        last_idle = idle_time
        last_total = total_time
    except Exception:
        pass

    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].split()[0])
                    mem[key] = val

            total_ram_kb = mem.get("MemTotal", 1)
            free_ram_kb = mem.get("MemFree", 0)
            buffers_kb = mem.get("Buffers", 0)
            cached_kb = mem.get("Cached", 0)
            avail_ram_kb = mem.get("MemAvailable", free_ram_kb + buffers_kb + cached_kb)

            used_ram_kb = total_ram_kb - avail_ram_kb
            used_gb = used_ram_kb / (1024 * 1024)
            total_gb = total_ram_kb / (1024 * 1024)
            ram_percentage = (used_ram_kb / total_ram_kb) * 100.0
    except Exception:
        pass

    return cpu_usage, used_gb, total_gb, ram_percentage


# ==============================================================================
# HÀM QUÉT PACKAGE TRÊN MÁY VÀ ĐỒNG BỘ DỮ LIỆU
# ==============================================================================
def get_installed_roblox_packages():
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
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as e:
        print(f"[X] Lỗi khi lưu file: {e}")


ACCOUNTS = load_accounts()


def sync_packages():
    global ACCOUNTS
    installed = get_installed_roblox_packages()

    updated_accounts = list(ACCOUNTS)
    existing_pkgs = [acc["package"] for acc in ACCOUNTS]

    for pkg in installed:
        if pkg not in existing_pkgs:
            updated_accounts.append({
                "package": pkg,
                "username": f"Player_{pkg.split('.')[-1]}",
                "place_id": 1537690962,
                "vip_link": "",
                "pkg_enabled": True,
                "client_enabled": True
            })

    ACCOUNTS = updated_accounts
    save_accounts(ACCOUNTS)


sync_packages()

last_ping = {acc["username"]: 0 for acc in ACCOUNTS}
has_pinged = {acc["username"]: False for acc in ACCOUNTS}


# ==============================================================================
# BÓC TÁCH LINK & KHỞI CHẠY APP
# ==============================================================================
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


def get_main_activity(pkg):
    try:
        cmd = f'su -c "cmd package resolve-activity --brief {pkg}"'
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode == 0 and res.stdout:
            lines = res.stdout.strip().splitlines()
            for line in lines:
                if "/" in line and not line.startswith("Priority"):
                    return line.strip()
    except Exception:
        pass
    return f"{pkg}/com.roblox.client.startup.ActivitySplash"


def restart_account(acc):
    username = acc["username"]
    pkg = acc["package"]
    place_id = acc.get("place_id", 1537690962)
    vip_link = acc.get("vip_link", "")

    now_str = time.strftime("%H:%M:%S")
    print(f"[{now_str}] Launching: {username} ({pkg})...")

    safe_kill_cmd = rf'su -c "am force-stop {pkg}; kill -9 \$(pgrep -f {pkg})"'
    subprocess.run(safe_kill_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for p_path in ["/sdcard/Delta/workspace/", "/sdcard/Android/data/", "/sdcard/"]:
        p_file = os.path.join(p_path, f"ping_{username}.txt")
        subprocess.run(f'su -c "rm -f {p_file}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    time.sleep(RESTART_DELAY)
    deep_link = parse_vip_link(vip_link, place_id)

    launch_cmd = f'su -c "am start -a android.intent.action.VIEW -d \\"{deep_link}\\" -p \\"{pkg}\\""'
    res = subprocess.run(launch_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if res.returncode != 0 or "Error" in res.stderr or "unable to resolve" in res.stderr.lower():
        main_activity = get_main_activity(pkg)
        fallback_cmd = f'su -c "am start -n {main_activity} -a android.intent.action.VIEW -d \\"{deep_link}\\""'
        res_fb = subprocess.run(fallback_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if res_fb.returncode != 0:
            monkey_cmd = f'su -c "monkey -p {pkg} -c android.intent.category.LAUNCHER 1"'
            subprocess.run(monkey_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"OK [{pkg}] Launched!")

    with ping_lock:
        last_ping[username] = time.time()
        has_pinged[username] = False


# ==============================================================================
# HÀM QUÉT FILE TÍN HIỆU
# ==============================================================================
def check_file_pings():
    search_paths = [
        "/sdcard/Delta/workspace/",
        "/sdcard/Android/data/",
        "/sdcard/"
    ]
    current_time = time.time()

    for path in search_paths:
        cmd = f'su -c "find {path} -name \\"ping_*.txt\\" 2>/dev/null"'
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, text=True)

        if res.returncode == 0 and res.stdout:
            files = res.stdout.strip().splitlines()
            for filepath in files:
                try:
                    filename = os.path.basename(filepath)
                    username = filename.replace("ping_", "").replace(".txt", "").strip()

                    cat_cmd = f'su -c "cat \\"{filepath}\\""'
                    c_res = subprocess.run(cat_cmd, shell=True, stdout=subprocess.PIPE, text=True)

                    if c_res.returncode == 0 and c_res.stdout.strip().isdigit():
                        file_timestamp = int(c_res.stdout.strip())
                        diff = current_time - file_timestamp

                        if 0 <= diff < 20:
                            with ping_lock:
                                if username in last_ping:
                                    last_ping[username] = current_time
                                    has_pinged[username] = True
                except Exception:
                    pass


# ==============================================================================
# GIAO DIỆN ASCII SUB-MENUS
# ==============================================================================
def manage_packages_menu():
    sync_packages()
    while True:
        clear_screen()
        print("=" * 50)
        print("      QUẢN LÝ PACKAGE ROBLOX CÓ TRÊN MÁY")
        print("=" * 50)
        
        if not ACCOUNTS:
            print(" [!] Không tìm thấy Package Roblox/Noka nào!")
        else:
            for i, acc in enumerate(ACCOUNTS, 1):
                status = "[ON] " if acc.get("pkg_enabled", True) else "[OFF]"
                print(f" [{i}] {status} | App: {acc['package']}")

        print("-" * 50)
        print(" [1-N] Nhập số để BẬT/TẮT Package tương ứng")
        print(" [88]  BẬT TẤT CẢ PACKAGE")
        print(" [99]  TẮT TẤT CẢ PACKAGE")
        print(" [0]   Quay lại Menu chính")
        print("=" * 50)

        try:
            choice = prompt("Lựa chọn: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

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
                ACCOUNTS[idx]["pkg_enabled"] = not ACCOUNTS[idx].get("pkg_enabled", True)
                save_accounts(ACCOUNTS)


def manage_clients_menu():
    while True:
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", True)]
        clear_screen()
        print("=" * 50)
        print("    DANH SÁCH CLIENT (PACKAGE ĐÃ CHỌN ON)")
        print("=" * 50)

        if not active_pkgs:
            print(" [!] Chưa có Package nào được BẬT ở Mục [5]!")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                status = "[RUNNING]" if acc.get("client_enabled", True) else "[STOPPED]"
                print(f" [{i}] {status:<9} | User: {acc['username']:<15} | App: {acc['package']}")

        print("-" * 50)
        print(" [1-N] Nhập số để Bật/Tắt trạng thái Client")
        print(" [0]   Quay lại Menu chính")
        print("=" * 50)

        try:
            choice = prompt("Lựa chọn: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                active_pkgs[idx]["client_enabled"] = not active_pkgs[idx].get("client_enabled", True)
                save_accounts(ACCOUNTS)


def set_username_menu():
    while True:
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", True)]
        clear_screen()
        print("=" * 50)
        print("          ĐỔI TÊN PLAYER (USERNAME)")
        print("=" * 50)

        if not active_pkgs:
            print(" [!] Hãy BẬT Package ở Mục [5] trước khi đổi tên!")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                print(f" [{i}] User: {acc['username']:<15} | App: {acc['package']}")

        print("-" * 50)
        print(" [1-N] Chọn Client để đổi tên Player tương ứng")
        print(" [0]   Quay lại Menu chính")
        print("=" * 50)

        try:
            choice = prompt("Lựa chọn: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                acc = active_pkgs[idx]
                old_name = acc["username"]
                new_name = prompt(f"\nNhập Username mới cho [{acc['package']}] (Cũ: {old_name}): ").strip()
                if new_name:
                    acc["username"] = new_name
                    save_accounts(ACCOUNTS)
                    print(f"[+] Đã đổi tên thành công: [{new_name}]")
                    time.sleep(1)


def config_server_menu():
    while True:
        active_pkgs = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", True)]
        clear_screen()
        print("=" * 50)
        print("       CẤU HÌNH GAME & SERVER CLIENT")
        print("=" * 50)

        if not active_pkgs:
            print(" [!] Hãy BẬT Package ở Mục [5] trước khi cài đặt!")
        else:
            for i, acc in enumerate(active_pkgs, 1):
                link_display = acc.get("vip_link", "").strip() or "[Public Server]"
                if len(link_display) > 20: link_display = link_display[:17] + "..."
                print(f" [{i}] {acc['username']:<15} | PlaceID: {acc.get('place_id', 1537690962)} | {link_display}")

        print("-" * 50)
        print(" [99]  Cài đặt cho TẤT CẢ Client đang mở")
        print(" [1-N] Chọn riêng từng Client để cài đặt")
        print(" [0]   Quay lại Menu chính")
        print("=" * 50)

        try:
            choice = prompt("Lựa chọn: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break
        elif choice == "99" and active_pkgs:
            inp = prompt("\nNhập PlaceID HOẶC Link Server VIP: ").strip()
            for acc in active_pkgs:
                if inp.isdigit(): acc["place_id"] = int(inp)
                else: acc["vip_link"] = inp
            save_accounts(ACCOUNTS)
            print("[+] Cập nhật thành công!")
            time.sleep(1)
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(active_pkgs):
                target_acc = active_pkgs[idx]
                inp = prompt(f"\nNhập PlaceID HOẶC Link VIP cho [{target_acc['username']}]: ").strip()
                if inp.isdigit(): target_acc["place_id"] = int(inp)
                else: target_acc["vip_link"] = inp
                save_accounts(ACCOUNTS)
                print("[+] Cập nhật thành công!")
                time.sleep(1)


# ==============================================================================
# MỤC 4: REJOIN AUTOMATION DASHBOARD
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
    runnable_accounts = [acc for acc in ACCOUNTS if acc.get("pkg_enabled", True) and acc.get("client_enabled", True)]

    if not runnable_accounts:
        print("\n[!] Không có Client nào đủ điều kiện chạy!")
        print("[!] Hãy kiểm tra lại Mục [5] và Mục [2].")
        prompt("\nNhấn Enter để quay lại Menu...")
        return

    server = MultithreadedTCPServer(("0.0.0.0", PORT), PingHandler)
    server.timeout = 1.0
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\n[+] Đang khởi chạy danh sách app...")
    for acc in runnable_accounts:
        restart_account(acc)
        time.sleep(LAUNCH_INTERVAL)

    try:
        while True:
            check_file_pings()
            cpu_p, used_gb, total_gb, ram_p = get_system_stats()
            current_time = time.time()
            clear_screen()
            now_str = time.strftime("%H:%M:%S")

            print("=" * 60)
            print(f" TERMUX REJOIN MONITOR | TIME: {now_str}")
            print(f" CPU: {cpu_p:.1f}%  |  RAM: {used_gb:.2f}GB / {total_gb:.2f}GB ({ram_p:.1f}%)")
            print("=" * 60)
            print(f" {'USER':<16} | {'APP':<12} | {'STATUS'}")
            print("-" * 60)

            for acc in runnable_accounts:
                user = acc["username"]
                pkg = acc["package"]

                disp_user = user[:15] if len(user) > 15 else user
                disp_pkg = pkg.replace("free.", "")[:11] if len(pkg) > 11 else pkg

                with ping_lock:
                    u_last_ping = last_ping.get(user, 0)
                    u_has_pinged = has_pinged.get(user, False)

                diff = int(current_time - u_last_ping)
                if u_has_pinged and diff <= MAX_NO_PING:
                    status_str = f"ONLINE ({diff}s ago)"
                else:
                    status_str = f"TIMEOUT ({diff}s/{MAX_NO_PING}s)"

                print(f" {disp_user:<16} | {disp_pkg:<12} | {status_str}")

            print("=" * 60)

            for acc in runnable_accounts:
                user = acc["username"]
                with ping_lock:
                    u_last_ping = last_ping.get(user, 0)

                if current_time - u_last_ping > MAX_NO_PING:
                    threading.Thread(target=restart_account, args=(acc,), daemon=True).start()
                    with ping_lock: last_ping[user] = current_time

            time.sleep(3)

    except KeyboardInterrupt:
        print("\n[!] Đã dừng chương trình.")
    finally:
        server.shutdown()
        server.server_close()
        sys.exit(0)


# ==============================================================================
# MENU CHÍNH
# ==============================================================================
if __name__ == "__main__":
    check_root_permission()

    menu_table = [
        ("1", "Cài đặt Game & Link Server Client"),
        ("2", "Quản lý Client (Bật/Tắt trạng thái)"),
        ("3", "Đổi Tên Player (Roblox Username)"),
        ("4", "Bắt đầu chạy kịch bản Rejoin"),
        ("5", "Quản lý Package trên máy (Bật/Tắt App)"),
        ("0", "Thoát chương trình")
    ]

    while True:
        clear_screen()
        print("=" * 50)
        print("              TERMUX REJOIN MANAGER")
        print("=" * 50)

        for key, text in menu_table:
            print(f" [{key}] {text}")

        print("=" * 50)

        try:
            choice = prompt("Lựa chọn (0-5): ").strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

        if choice == "1": config_server_menu()
        elif choice == "2": manage_clients_menu()
        elif choice == "3": set_username_menu()
        elif choice == "4":
            clear_screen()
            run_manager()
            break
        elif choice == "5": manage_packages_menu()
        elif choice == "0":
            print("Đã thoát chương trình.")
            sys.exit(0)
