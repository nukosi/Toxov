import os
import sys
import json
import time
import datetime
import subprocess
import ctypes
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox

CONFIG_DIR  = os.path.join(os.environ["APPDATA"], "nukosisnsblocker")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HOSTS_FILE  = r"C:\Windows\System32\drivers\etc\hosts"
BLOCK_TAG   = "# nukosisnsblocker"
JST         = datetime.timezone(datetime.timedelta(hours=9))


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def relaunch_as_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()


def first_run_setup():
    root = tk.Tk()
    root.withdraw()

    url = simpledialog.askstring(
        "nukosisnsblocker セットアップ",
        "Webサイトの「PC連携トークン」URLを貼り付けてください:",
        parent=root,
    )

    if not url or not url.strip():
        messagebox.showerror("エラー", "URLが入力されませんでした。")
        sys.exit(1)

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"cloud_url": url.strip()}, f)

    register_autostart()

    messagebox.showinfo(
        "セットアップ完了",
        "設定が完了しました！\nPC起動時に自動でブロックが動作します。",
    )
    root.destroy()


def register_autostart():
    exe = sys.executable
    ps = f"""
$action   = New-ScheduledTaskAction -Execute '{exe}'
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$principal= New-ScheduledTaskPrincipal -UserId '{os.environ["USERNAME"]}' -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'nukosisnsblocker' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
"""
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def load_local_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_cloud_config(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def should_be_blocked(config):
    now = datetime.datetime.now(JST).time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


def block(sites):
    with open(HOSTS_FILE, "r") as f:
        content = f.read()
    with open(HOSTS_FILE, "a") as f:
        for site in sites:
            entry = f"0.0.0.0 {site} {BLOCK_TAG}\n"
            if entry not in content:
                f.write(entry)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)


def unblock():
    with open(HOSTS_FILE, "r") as f:
        lines = f.readlines()
    with open(HOSTS_FILE, "w") as f:
        for line in lines:
            if BLOCK_TAG not in line:
                f.write(line)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)


def main():
    if not is_admin():
        relaunch_as_admin()
        return

    if not os.path.exists(CONFIG_FILE):
        first_run_setup()

    local  = load_local_config()
    cloud_url = local["cloud_url"]
    current_state = None

    while True:
        try:
            config = fetch_cloud_config(cloud_url)
            emergency = config.get("emergency_unblock", False)

            if emergency:
                if current_state is not False:
                    unblock()
                    current_state = False
            else:
                should_block = should_be_blocked(config)
                if should_block != current_state:
                    if should_block:
                        block(config.get("sites", []))
                    else:
                        unblock()
                    current_state = should_block

        except Exception:
            pass

        time.sleep(60)


if __name__ == "__main__":
    main()
