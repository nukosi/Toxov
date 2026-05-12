import os
import sys
import json
import time
import datetime
import subprocess
import ctypes
import winreg
import threading
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox
import pystray
from PIL import Image, ImageDraw
from comments import get_comment

# ドメイン取得時はここだけ変える
SERVER_BASE = "https://web-production-ed8c9.up.railway.app"

# サーバー側の AGENT_SECRET 環境変数と同じ値を設定すること
# 配布前に必ずRailwayで AGENT_SECRET を設定し、この値を合わせてビルドし直す
AGENT_SECRET = "toxov-prod-abc123xyz"

CONFIG_DIR  = os.path.join(os.environ["APPDATA"], "Toxov")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
HOSTS_FILE  = r"C:\Windows\System32\drivers\etc\hosts"
# hostsファイルに追記する行の末尾に付けるタグ。unblock時にこのタグで自分が書いた行だけ削除する
BLOCK_TAG     = "# Toxov"
# アプリ名変更前の旧タグ。hostsに残留している場合も除去する
OLD_BLOCK_TAG = "# nukosisnsblocker"
JST         = datetime.timezone(datetime.timedelta(hours=9))
POLL_INTERVAL = 30  # seconds

# 全APIリクエストに付与する認証ヘッダー
_API_HEADERS = {"X-Toxov-Key": AGENT_SECRET}


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


def resolve_connect_code(code: str) -> str | None:
    # 6文字の接続コードをサーバーに送ってconfig URLを取得する
    try:
        res = requests.get(
            f"{SERVER_BASE}/api/connect/{code.strip().upper()}",
            headers=_API_HEADERS, timeout=10,
        )
        if res.status_code == 200:
            return res.json().get("config_url")
    except Exception:
        pass
    return None


def first_run_setup():
    # 初回起動時のみ実行。6文字の接続コードを入力させてconfig URLをAppDataに保存する
    root = tk.Tk()
    root.withdraw()

    while True:
        code = simpledialog.askstring(
            "Toxov セットアップ",
            "Webサイトの「PC連携」に表示されている\n6文字のコードを入力してください:",
            parent=root,
        )
        if not code:
            messagebox.showerror("エラー", "コードが入力されませんでした。")
            sys.exit(1)

        cloud_url = resolve_connect_code(code)
        if cloud_url:
            break
        messagebox.showerror("エラー", "コードが正しくありません。再度入力してください。")

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"cloud_url": cloud_url}, f)

    register_autostart()

    messagebox.showinfo(
        "Toxov セットアップ完了",
        "設定が完了しました！\nPC起動時に自動でブロックが動作します。",
    )
    root.destroy()


def register_autostart():
    # Windowsタスクスケジューラにログオン時の自動起動を登録する
    # RunLevel Highest = 管理者として起動（UACダイアログなし）
    # 旧名称のタスクが残っている場合は先に削除する
    exe = sys.executable
    ps = f"""
Unregister-ScheduledTask -TaskName 'nukosisnsblocker' -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'CutNet' -Confirm:$false -ErrorAction SilentlyContinue
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


def kill_edge_connections(log=None):
    # EdgeのNetwork Serviceプロセスだけをwmicで特定して終了する
    # NetworkServiceはEdgeに自動再起動され、再起動後は空のDNSキャッシュで動作するためhostsが即時反映される
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='msedge.exe' and commandline like '%network.mojom.NetworkService%'",
             "get", "ProcessId", "/format:value"],
            capture_output=True, text=True, timeout=15,
        )
        killed = 0
        for line in result.stdout.strip().splitlines():
            if "=" in line:
                pid = line.split("=")[-1].strip()
                if pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                    killed += 1
        if log:
            log(f"[kill_edge] NetworkService {killed}件終了")
    except Exception as e:
        if log:
            log(f"[kill_edge] エラー: {e}")


def unblock(log=None):
    # --- Edge/ChromeのDoHポリシーを削除してデフォルト動作に戻す ---
    set_doh_policy(disable=False)

    # --- Webサイト解除 ---
    # BLOCK_TAGが含まれる行だけ除いて書き直す
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        with open(HOSTS_FILE, "w", encoding="utf-8") as f:
            for line in lines:
                # 旧タグ（nukosisnsblocker）の残留エントリも合わせて除去する
                if BLOCK_TAG not in line and OLD_BLOCK_TAG not in line:
                    f.write(line)
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    except Exception as e:
        if log:
            log(f"[unblock] hostsファイルエラー: {e}")

    # --- アプリ解除：Toxovという名前のファイアウォールルールをすべて削除 ---
    result = subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", "name=Toxov"],
        capture_output=True, text=True
    )
    if log and result.returncode != 0:
        log(f"[unblock] firewall削除エラー rc={result.returncode} {result.stderr.strip()}")


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


# ポーリングスレッドとトレイ間でブロック状態を共有するための辞書
_tray_state = {"blocking": None}


def make_icon(blocking) -> Image.Image:
    # ブロック中は赤、解除中は緑のシンプルな円アイコン
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = "#ff4444" if blocking else "#44cc44"
    draw.ellipse([6, 6, 58, 58], fill=color)
    return img


def setup_tray(add_url: str, log) -> pystray.Icon:
    def status_text(item):
        if _tray_state["blocking"] is True:
            return "ブロック中"
        if _tray_state["blocking"] is False:
            return "解除中"
        return "起動中..."

    def add_app(icon, item):
        # 実行中プロセスをWindows FormsのListBoxで表示して選ばせる
        # C:\Windows\ 系とWindowsAppsは除外してユーザーアプリだけを表示する
        # base64エンコードで渡すことで日本語や特殊文字のエスケープ問題を回避する
        import base64
        ps_script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$seen = @{}
$items = [System.Collections.Generic.List[PSCustomObject]]::new()
Get-Process | ForEach-Object {
    try {
        $path = $_.MainModule.FileName
        if ($path -and
            -not $path.StartsWith('C:\Windows\') -and
            -not $path.StartsWith('C:\Program Files\WindowsApps\') -and
            -not $seen.ContainsKey($path)) {
            $seen[$path] = $true
            $items.Add([PSCustomObject]@{ Label = [System.IO.Path]::GetFileName($path); Path = $path })
        }
    } catch {}
}
$items = $items | Sort-Object Label

$form = New-Object System.Windows.Forms.Form
$form.Text = 'ブロックするアプリを選択'
$form.Size = New-Object System.Drawing.Size(520, 460)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$hint = New-Object System.Windows.Forms.Label
$hint.Text = 'ブロックしたいアプリを起動してからこの一覧で選んでください'
$hint.Location = New-Object System.Drawing.Point(12, 10)
$hint.Size = New-Object System.Drawing.Size(480, 18)
$hint.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$form.Controls.Add($hint)

# 各exeのアイコンをImageListに登録する
$imgList = New-Object System.Windows.Forms.ImageList
$imgList.ImageSize = New-Object System.Drawing.Size(24, 24)
$imgList.ColorDepth = 'Depth32Bit'

$lv = New-Object System.Windows.Forms.ListView
$lv.Location = New-Object System.Drawing.Point(12, 34)
$lv.Size = New-Object System.Drawing.Size(480, 370)
$lv.View = 'Details'
$lv.FullRowSelect = $true
$lv.MultiSelect = $false
$lv.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$lv.SmallImageList = $imgList
$lv.HeaderStyle = 'None'
$lv.Columns.Add('name', 460) | Out-Null

$idx = 0
foreach ($i in $items) {
    try {
        $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($i.Path)
        $imgList.Images.Add($icon) | Out-Null
        $li = New-Object System.Windows.Forms.ListViewItem($i.Label, $idx)
        $idx++
    } catch {
        $li = New-Object System.Windows.Forms.ListViewItem($i.Label)
    }
    $li.Tag = $i.Path
    $lv.Items.Add($li) | Out-Null
}
$form.Controls.Add($lv)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = '追加'
$ok.Location = New-Object System.Drawing.Point(420, 412)
$ok.Size = New-Object System.Drawing.Size(72, 28)
$ok.DialogResult = 'OK'
$form.Controls.Add($ok)
$form.AcceptButton = $ok

if ($form.ShowDialog() -eq 'OK' -and $lv.SelectedItems.Count -gt 0) {
    $lv.SelectedItems[0].Tag
}
"""
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=120,
        )
        path = result.stdout.strip()
        if not path:
            return
        try:
            res = requests.post(add_url, json={"path": path},
                                headers=_API_HEADERS, timeout=5)
            if res.ok:
                notify(f"追加: {os.path.basename(path)}")
            else:
                notify("追加に失敗しました（上限に達している可能性があります）")
        except Exception as e:
            log(f"アプリ追加エラー: {e}")

    def quit_action(icon, item):
        # 終了時にhostsとファイアウォールを掃除してからアイコンを閉じる
        unblock(log)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("アプリを追加", add_app),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("終了", quit_action),
    )
    return pystray.Icon("Toxov", make_icon(False), "Toxov", menu)


def config_stream(url, log):
    """
    設定取得インターフェース。
    将来このジェネレータをWebSocket版に丸ごと置き換える。
    """
    while True:
        try:
            response = requests.get(url, headers=_API_HEADERS, timeout=10)
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
            unblock(log)
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
            unblock(log)
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

    # 旧名称の自動起動タスクが残っていれば毎回削除する（リネーム移行対応）
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Unregister-ScheduledTask -TaskName 'nukosisnsblocker' -Confirm:$false -EA SilentlyContinue;"
         "Unregister-ScheduledTask -TaskName 'CutNet' -Confirm:$false -EA SilentlyContinue"],
        capture_output=True,
    )

    if not os.path.exists(CONFIG_FILE):
        first_run_setup()

    local     = load_local_config()
    cloud_url = local["cloud_url"]

    LOG_FILE = os.path.join(CONFIG_DIR, "agent.log")

    def log(msg):
        now = datetime.datetime.now(JST).strftime("%H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {msg}\n")

    # ログにはトークン末尾8文字のみ記録してフルトークンの漏洩を防ぐ
    masked_url = cloud_url[:-8] + "..." + cloud_url[-4:] if len(cloud_url) > 12 else "***"
    log(f"起動 URL={masked_url}")

    # config URLからlog・アプリ追加のURLを導出する（トークンは共通）
    log_url = cloud_url.replace("/api/config/", "/api/log/")
    add_url = cloud_url.replace("/api/config/", "/api/apps/add/")

    # トレイアイコンをメインスレッドで動かすため、ポーリングループを別スレッドに移す
    tray = setup_tray(add_url, log)

    def polling_loop():
        current_state  = None
        last_version   = None
        prev_emergency = False  # 前回pollで緊急解除中だったか
        for config in config_stream(cloud_url, log):
            emergency = config.get("emergency_unblock", False)
            current_state, last_version, events = apply_config(
                config, current_state, last_version, log
            )
            # ブロック状態をトレイアイコンとメニューテキストに反映する
            _tray_state["blocking"] = current_state
            tray.icon = make_icon(current_state is True)
            # pystrayのWindowsバックエンドはメニューテキストを自動更新しないため明示的に更新する
            try:
                tray.update_menu()
            except Exception:
                pass
            for event in events:
                if event == "block_start":
                    if prev_emergency:
                        kill_edge_connections(log)
                        notify("再ブロック完了")
                    else:
                        notify(f"ブロック開始  {get_comment('block_start')}")
                elif event == "block_end":
                    notify(f"ブロック終了  {get_comment('block_end')}")
                try:
                    requests.post(log_url, json={"event": event},
                                  headers=_API_HEADERS, timeout=5)
                except Exception:
                    pass
            prev_emergency = emergency

    # daemon=True にするとメインスレッド（トレイ）が終了したら一緒に終了する
    threading.Thread(target=polling_loop, daemon=True).start()

    # pystray はメインスレッドをブロックして動作する（Windows の win32 バックエンド要件）
    tray.run()


if __name__ == "__main__":
    main()
