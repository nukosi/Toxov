import os
import sys
import json
import time
import datetime
import subprocess
import ctypes
import winreg
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox
from comments import get_comment

CONFIG_DIR  = os.path.join(os.environ["APPDATA"], "Toxov")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HOSTS_FILE  = r"C:\Windows\System32\drivers\etc\hosts"
# hostsファイルに追記する行の末尾に付けるタグ。unblock時にこのタグで自分が書いた行だけ削除する
BLOCK_TAG   = "# Toxov"
JST         = datetime.timezone(datetime.timedelta(hours=9))
POLL_INTERVAL = 30  # seconds


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def relaunch_as_admin():
    # hostsファイルの書き換えには管理者権限が必要なので、UACで昇格して自分自身を再起動する
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()


def ensure_single_instance():
    # Windowsの名前付きMutexで二重起動を防止する
    # CreateMutexWはMutexが既に存在する場合もハンドルを返すが、GetLastErrorが183(ERROR_ALREADY_EXISTS)になる
    # 呼び出し元でmutexの参照を保持し続けること（GCされるとMutexが解放されて二重起動防止が無効になる）
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\Toxov")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return None  # 既に別インスタンスが動いている
    return mutex


def first_run_setup():
    # 初回起動時のみ実行。PC連携トークンURLを入力させてAppDataに保存する
    root = tk.Tk()
    root.withdraw()

    url = simpledialog.askstring(
        "Toxov セットアップ",
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
        "Toxov セットアップ完了",
        "設定が完了しました！\nPC起動時に自動でブロックが動作します。",
    )
    root.destroy()


def register_autostart():
    # Windowsタスクスケジューラにログオン時の自動起動を登録する
    # RunLevel Highest = 管理者として起動（UACダイアログなし）
    exe = sys.executable
    ps = f"""
$action   = New-ScheduledTaskAction -Execute '{exe}'
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$principal= New-ScheduledTaskPrincipal -UserId '{os.environ["USERNAME"]}' -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'Toxov' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
"""
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def load_local_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def set_doh_policy(disable: bool):
    # Edge・ChromeのDNS over HTTPSをレジストリのグループポリシーで制御する
    # disable=True でDoHを強制オフ（hostsファイルが有効になる）
    # disable=False でポリシーを削除しブラウザのデフォルト動作に戻す
    targets = [
        r"SOFTWARE\Policies\Microsoft\Edge",
        r"SOFTWARE\Policies\Google\Chrome",
    ]
    for key_path in targets:
        try:
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                if disable:
                    winreg.SetValueEx(key, "DnsOverHttpsMode", 0, winreg.REG_SZ, "off")
                else:
                    try:
                        winreg.DeleteValue(key, "DnsOverHttpsMode")
                    except FileNotFoundError:
                        pass
        except Exception:
            pass


def should_be_blocked(config):
    """ローカル時刻で判定。サーバー不要。"""
    now = datetime.datetime.now(JST).time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


def block(sites, apps):
    # --- Edge/ChromeのDoHを無効化（hostsファイルが機能するようにする） ---
    set_doh_policy(disable=True)

    # --- Webサイト：hostsファイルで遮断 ---
    # 既存のエントリと重複しないよう先に全文を読んでから追記する
    with open(HOSTS_FILE, "r") as f:
        content = f.read()
    with open(HOSTS_FILE, "a") as f:
        for site in sites:
            entry = f"0.0.0.0 {site} {BLOCK_TAG}\n"
            if entry not in content:
                f.write(entry)
    # ブラウザの内部DNSキャッシュはタブを閉じるまで残るが、OSキャッシュはここでクリアする
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)

    # --- アプリ：Windowsファイアウォールで遮断 ---
    # 既存のToxovルールを一旦全削除してから再登録する（削除されたappの残留を防ぐ）
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", "name=Toxov"],
        capture_output=True
    )
    for path in apps:
        if os.path.exists(path):
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                "name=Toxov", "dir=out", "action=block", f"program={path}"
            ], capture_output=True)


def unblock():
    # --- Edge/ChromeのDoHポリシーを削除してデフォルト動作に戻す ---
    set_doh_policy(disable=False)

    # --- Webサイト解除 ---
    # BLOCK_TAGが含まれる行だけ除いて書き直す
    with open(HOSTS_FILE, "r") as f:
        lines = f.readlines()
    with open(HOSTS_FILE, "w") as f:
        for line in lines:
            if BLOCK_TAG not in line:
                f.write(line)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)

    # --- アプリ解除：Toxovという名前のファイアウォールルールをすべて削除 ---
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", "name=Toxov"],
        capture_output=True
    )


def notify(message):
    # Windows PowerShellのバルーン通知（追加ライブラリ不要）
    # NotifyIconはGCされると消えるので Start-Sleep で表示を維持してから破棄する
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.BalloonTipTitle = 'Toxov';"
        f"$n.BalloonTipText = '{message}';"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(5000);"
        "Start-Sleep -Seconds 3;"
        "$n.Dispose()"
    )
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
        # コンソールウィンドウを出さない
        creationflags=0x08000000,
    )


def config_stream(url, log):
    """
    設定取得インターフェース。
    将来このジェネレータをWebSocket版に丸ごと置き換える。
    """
    while True:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            yield response.json()
        except Exception as e:
            log(f"エラー: {e}")
        time.sleep(POLL_INTERVAL)


def apply_config(config, current_state, last_version, log):
    """
    ローカル判定のみ。サーバーの時刻・状態に依存しない。
    戻り値: (new_state, new_last_version, events)
    """
    version        = config.get("version")
    emergency      = config.get("emergency_unblock", False)
    # versionが変わったときだけblock()を呼び直してサイト一覧の変更を反映する
    config_changed = version != last_version
    events         = []

    log(f"poll v={version} emergency={emergency} state={current_state}")

    if emergency:
        if current_state is not False:
            unblock()
        # current_state が True のときだけログに残す（起動時の None → False は記録しない）
        if current_state is True:
            log("緊急解除 実行")
            events.append("emergency_unblock")
        return False, version, events

    should_block = should_be_blocked(config)

    if should_block:
        if config_changed or current_state is not True:
            block(config.get("sites", []), config.get("apps", []))
        # False → True の遷移のみ記録（起動時の None → True は記録しない）
        if current_state is False:
            log("ブロック 実行")
            events.append("block_start")
        return True, version, events
    else:
        if current_state is not False:
            unblock()
        # True → False の遷移のみ記録（起動時の None → False は記録しない）
        if current_state is True:
            log("解除 実行")
            events.append("block_end")
        return False, version, events


def main():
    if not is_admin():
        relaunch_as_admin()
        return

    # 管理者昇格後に二重起動チェック（非管理者プロセスはすでにsys.exit済みなので競合しない）
    mutex = ensure_single_instance()
    if mutex is None:
        sys.exit(0)

    if not os.path.exists(CONFIG_FILE):
        first_run_setup()

    local     = load_local_config()
    cloud_url = local["cloud_url"]

    LOG_FILE = os.path.join(CONFIG_DIR, "agent.log")

    def log(msg):
        now = datetime.datetime.now(JST).strftime("%H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {msg}\n")

    log(f"起動 URL={cloud_url}")

    # config URLからlogのURLを導出する（トークンは共通）
    log_url       = cloud_url.replace("/api/config/", "/api/log/")
    current_state = None
    last_version  = None

    for config in config_stream(cloud_url, log):
        current_state, last_version, events = apply_config(
            config, current_state, last_version, log
        )
        for event in events:
            # ブロック開始・終了をバルーン通知で知らせる
            if event == "block_start":
                notify(f"ブロック開始  {get_comment('block_start')}")
            elif event == "block_end":
                notify("ブロック終了しました")
            try:
                requests.post(log_url, json={"event": event}, timeout=5)
            except Exception:
                pass


if __name__ == "__main__":
    main()
