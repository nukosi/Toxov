import os
import sys
import json
import time
import datetime
import subprocess
import ctypes
import winreg
import threading
import shutil
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox
import pystray
from PIL import Image, ImageDraw
from comments import get_comment

# エージェントのバージョン。サーバーの LATEST_AGENT_VERSION と比較して自動更新する
VERSION = "2.0.0"

# ドメイン取得時はここだけ変える
SERVER_BASE = "https://web-production-ed8c9.up.railway.app"

# サーバー側の AGENT_SECRET 環境変数と同じ値を設定すること
# 配布前に必ずRailwayで AGENT_SECRET を設定し、この値を合わせてビルドし直す
AGENT_SECRET = "toxov-prod-abc123xyz"

CONFIG_DIR    = os.path.join(os.environ["APPDATA"], "Toxov")
CONFIG_FILE   = os.path.join(CONFIG_DIR, "config.json")
# 自動更新・Task Scheduler登録の固定インストールパス
# 初回起動時にここへ自分自身をコピーし、以後はここから起動する
INSTALL_PATH  = os.path.join(CONFIG_DIR, "Toxov.exe")
# サーバー設定のローカルキャッシュ。起動直後の即時ブロック適用に使う
CACHE_FILE      = os.path.join(CONFIG_DIR, "last_config.json")
# ファイアウォールルールが存在するかを記録するフラグファイル
# これがない場合は netsh delete をスキップして起動時の遅延（~27秒）を防ぐ
FIREWALL_FLAG   = os.path.join(CONFIG_DIR, "firewall_active.flag")
HOSTS_FILE  = r"C:\Windows\System32\drivers\etc\hosts"
# hostsファイルに追記する行の末尾に付けるタグ。unblock時にこのタグで自分が書いた行だけ削除する
BLOCK_TAG     = "# Toxov"
# アプリ名変更前の旧タグ。hostsに残留している場合も除去する
OLD_BLOCK_TAG = "# nukosisnsblocker"
JST         = datetime.timezone(datetime.timedelta(hours=9))
POLL_INTERVAL = 30  # seconds

# 全APIリクエストに付与する認証ヘッダー
_API_HEADERS = {"X-Toxov-Key": AGENT_SECRET}

STRINGS = {
    "ja": {
        "setup_title":      "Toxov セットアップ",
        "setup_prompt":     "Webサイトの「PC連携」に表示されている\n6文字のコードを入力してください:",
        "err_title":        "エラー",
        "err_no_code":      "コードが入力されませんでした。",
        "err_bad_code":     "コードが正しくありません。再度入力してください。",
        "setup_done_title": "Toxov セットアップ完了",
        "setup_done_msg":   "設定が完了しました！\nPC起動時に自動でブロックが動作します。",
        "status_blocking":  "ブロック中",
        "status_unblocked": "解除中",
        "status_starting":  "起動中...",
        "menu_add_app":     "アプリを追加",
        "menu_quit":        "終了",
        "ps_form_title":    "ブロックするアプリを選択",
        "ps_form_hint":     "ブロックしたいアプリを起動してからこの一覧で選んでください",
        "ps_btn_add":       "追加",
        "notify_added":     "追加: {name}",
        "notify_add_fail":  "追加に失敗しました（上限に達している可能性があります）",
        "notify_reblock":   "再ブロック完了",
        "notify_block_start": "ブロック開始  {comment}",
        "notify_block_end":   "ブロック終了  {comment}",
        "log_emergency":    "緊急解除 実行",
        "log_block":        "ブロック 実行",
        "log_unblock":      "解除 実行",
    },
    "en": {
        "setup_title":      "Toxov Setup",
        "setup_prompt":     'Enter the 6-character code shown\non the "PC Link" page of the website:',
        "err_title":        "Error",
        "err_no_code":      "No code was entered.",
        "err_bad_code":     "Incorrect code. Please try again.",
        "setup_done_title": "Toxov Setup Complete",
        "setup_done_msg":   "Setup complete!\nBlocking will start automatically when your PC boots.",
        "status_blocking":  "Blocking",
        "status_unblocked": "Unblocked",
        "status_starting":  "Starting...",
        "menu_add_app":     "Add App",
        "menu_quit":        "Quit",
        "ps_form_title":    "Select App to Block",
        "ps_form_hint":     "Launch the app you want to block, then select it from this list",
        "ps_btn_add":       "Add",
        "notify_added":     "Added: {name}",
        "notify_add_fail":  "Failed to add app (you may have reached the limit)",
        "notify_reblock":   "Re-block complete",
        "notify_block_start": "Blocking started  {comment}",
        "notify_block_end":   "Blocking ended  {comment}",
        "log_emergency":    "Emergency unblock executed",
        "log_block":        "Block started",
        "log_unblock":      "Block ended",
    },
}


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


def detect_lang() -> str:
    # GetUserDefaultUILanguage returns a LANGID; primary language 0x11 = Japanese
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ja" if (langid & 0xFF) == 0x11 else "en"
    except Exception:
        return "ja"


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
    lang = detect_lang()
    s = STRINGS[lang]

    root = tk.Tk()
    root.withdraw()

    while True:
        code = simpledialog.askstring(
            s["setup_title"],
            s["setup_prompt"],
            parent=root,
        )
        if not code:
            messagebox.showerror(s["err_title"], s["err_no_code"])
            sys.exit(1)

        cloud_url = resolve_connect_code(code)
        if cloud_url:
            break
        messagebox.showerror(s["err_title"], s["err_bad_code"])

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"cloud_url": cloud_url, "lang": lang}, f)

    register_autostart()

    messagebox.showinfo(s["setup_done_title"], s["setup_done_msg"])
    root.destroy()


def register_autostart():
    # Windowsタスクスケジューラにログオン時の自動起動を登録する
    # RunLevel Highest = 管理者として起動（UACダイアログなし）
    # 旧名称のタスクが残っている場合は先に削除する
    exe = INSTALL_PATH  # 常に固定インストールパスで登録する
    ps = f"""
Unregister-ScheduledTask -TaskName 'nukosisnsblocker' -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'CutNet' -Confirm:$false -ErrorAction SilentlyContinue
$action    = New-ScheduledTaskAction -Execute '{exe}'
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User '{os.environ["USERNAME"]}'
$trigBoot  = New-ScheduledTaskTrigger -AtStartup
$trigBoot.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId '{os.environ["USERNAME"]}' -RunLevel Highest -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Toxov' -Action $action -Trigger @($trigLogon,$trigBoot) -Principal $principal -Settings $settings -Force
"""
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def ensure_installed():
    """
    exe が INSTALL_PATH から起動されていない場合、そこへコピーして再起動する。
    これにより Task Scheduler の登録パスが常に固定され、自動更新も正しく機能する。
    """
    if not getattr(sys, "frozen", False):
        return  # スクリプト実行時はスキップ
    current = os.path.abspath(sys.executable)
    target  = os.path.abspath(INSTALL_PATH)
    if current.lower() == target.lower():
        return  # 正しいパスから起動済み

    # 別インスタンスが既に INSTALL_PATH で動いていれば何もせず終了する
    test_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\Toxov")
    already_running = ctypes.windll.kernel32.GetLastError() == 183
    ctypes.windll.kernel32.CloseHandle(test_mutex)
    if already_running:
        sys.exit(0)

    # INSTALL_PATH へコピーして再起動
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        shutil.copy2(current, target)
    except Exception:
        return  # コピー失敗時はそのまま継続
    ctypes.windll.shell32.ShellExecuteW(None, "runas", target, None, None, 1)
    sys.exit(0)


def _is_newer(remote: str, local: str) -> bool:
    """バージョン文字列を比較して remote が local より新しいか判定する。"""
    try:
        return (tuple(int(x) for x in remote.split(".")) >
                tuple(int(x) for x in local.split(".")))
    except Exception:
        return False


def apply_update(url: str, log):
    """
    新バージョンの exe をダウンロードし、バッチスクリプト経由で自分自身を差し替えて再起動する。
    バッチスクリプトは現プロセス終了後に実行されるため、実行中の exe を上書きできる。
    """
    new_exe  = INSTALL_PATH + ".new"
    bat_path = os.path.join(CONFIG_DIR, "update.bat")
    try:
        log(f"update: {VERSION} → downloading new version...")
        r = requests.get(url, timeout=120, stream=True, headers=_API_HEADERS)
        r.raise_for_status()
        with open(new_exe, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        log("update: download complete, applying...")
        # バッチスクリプト: 現プロセス終了を3秒待ってから差し替え・再起動する
        bat = (
            "@echo off\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            f'copy /y "{new_exe}" "{INSTALL_PATH}"\r\n'
            f'del "{new_exe}"\r\n'
            f'start "" "{INSTALL_PATH}"\r\n'
            'del "%~f0"\r\n'
        )
        with open(bat_path, "w", encoding="ascii") as f:
            f.write(bat)
        subprocess.Popen(
            [bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
        sys.exit(0)  # バッチが引き継ぐのでクリーンアップ不要
    except Exception as e:
        log(f"update failed: {e}")
        for p in (new_exe, bat_path):
            try:
                os.remove(p)
            except Exception:
                pass


def load_local_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cached_config(config: dict):
    """次回起動時の即時適用用にサーバー設定をローカルキャッシュする。"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception:
        pass


def load_cached_config() -> dict | None:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _set_firewall_flag(active: bool):
    """ファイアウォールルールの有無をフラグファイルに記録する。"""
    try:
        if active:
            open(FIREWALL_FLAG, 'w').close()
        elif os.path.exists(FIREWALL_FLAG):
            os.remove(FIREWALL_FLAG)
    except Exception:
        pass


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
            # 保存時に normalize_domain でapexドメインに正規化済みのため、
            # www. / m. / mobile. バリエーションをここで展開してすべてブロックする
            variants = [site]
            if not site.startswith("www."):
                variants.append(f"www.{site}")
            if not site.startswith("m."):
                variants.append(f"m.{site}")
            for domain in variants:
                entry = f"0.0.0.0 {domain} {BLOCK_TAG}\n"
                if entry not in content:
                    f.write(entry)
    # ブラウザの内部DNSキャッシュはタブを閉じるまで残るが、OSキャッシュはここでクリアする
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)

    # --- アプリ：Windowsファイアウォールで遮断 ---
    # フラグがある場合のみ既存ルールを削除（フラグなし = ルール未登録 → deleteは不要かつ遅い）
    if os.path.exists(FIREWALL_FLAG):
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
    # ルール追加完了をフラグに記録（次回 unblock/block 時のスキップ判定に使う）
    _set_firewall_flag(True)


def kill_edge_connections(log=None):
    # Edge・ChromeのNetworkServiceプロセスを終了してDNSキャッシュをクリアする
    # NetworkServiceは各ブラウザに自動再起動され、再起動後は空のDNSキャッシュでhostsが即時反映される
    for exe in ("msedge.exe", "chrome.exe"):
        try:
            result = subprocess.run(
                ["wmic", "process", "where",
                 f"name='{exe}' and commandline like '%network.mojom.NetworkService%'",
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
            if log and killed > 0:
                log(f"[kill_browser] {exe} NetworkService {killed} terminated")
        except Exception as e:
            if log:
                log(f"[kill_browser] {exe} error: {e}")


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
            log(f"[unblock] hosts error: {e}")

    # --- アプリ解除：フラグがある場合のみ netsh でルールを削除する ---
    # フラグなし = ルール未登録 → netsh delete（~27秒）をスキップして即時完了
    if os.path.exists(FIREWALL_FLAG):
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", "name=Toxov"],
            capture_output=True, text=True
        )
        if log and result.returncode != 0:
            log(f"[unblock] firewall error rc={result.returncode} {result.stderr.strip()}")
        _set_firewall_flag(False)


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


def _load_tray_base() -> Image.Image:
    # PyInstaller bundleとスクリプト実行の両方に対応したパス解決
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "tray_icon.png")
    if os.path.exists(path):
        return Image.open(path).convert("RGBA").resize((64, 64), Image.LANCZOS)
    # フォールバック: シンプルな六角形
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    return img


def make_icon(blocking) -> Image.Image:
    # Toxovロゴをベースに、右下にブロック状態の小ドットを重ねる
    img  = _load_tray_base().copy()
    draw = ImageDraw.Draw(img)
    color = "#ff4444" if blocking else "#44cc44"
    draw.ellipse([44, 44, 60, 60], fill=color, outline="#000000", width=1)
    return img


def setup_tray(add_url: str, log, lang: str) -> pystray.Icon:
    s = STRINGS[lang]

    def status_text(item):
        if _tray_state["blocking"] is True:
            return s["status_blocking"]
        if _tray_state["blocking"] is False:
            return s["status_unblocked"]
        return s["status_starting"]

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
$form.Text = '__FORM_TITLE__'
$form.Size = New-Object System.Drawing.Size(520, 460)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$hint = New-Object System.Windows.Forms.Label
$hint.Text = '__HINT_TEXT__'
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
$ok.Text = '__BTN_TEXT__'
$ok.Location = New-Object System.Drawing.Point(420, 412)
$ok.Size = New-Object System.Drawing.Size(72, 28)
$ok.DialogResult = 'OK'
$form.Controls.Add($ok)
$form.AcceptButton = $ok

if ($form.ShowDialog() -eq 'OK' -and $lv.SelectedItems.Count -gt 0) {
    $lv.SelectedItems[0].Tag
}
"""
        ps_script = (
            ps_script
            .replace("__FORM_TITLE__", s["ps_form_title"])
            .replace("__HINT_TEXT__", s["ps_form_hint"])
            .replace("__BTN_TEXT__", s["ps_btn_add"])
        )
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
                notify(s["notify_added"].format(name=os.path.basename(path)))
            else:
                notify(s["notify_add_fail"])
        except Exception as e:
            log(f"add app error: {e}")

    def quit_action(icon, item):
        # 終了時にhostsとファイアウォールを掃除してからアイコンを閉じる
        unblock(log)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(s["menu_add_app"], add_app),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(s["menu_quit"], quit_action),
    )
    return pystray.Icon("Toxov", make_icon(False), "Toxov", menu)


def config_stream(url, log):
    """
    設定取得インターフェース。
    将来このジェネレータをWebSocket版に丸ごと置き換える。
    サーバー不達が続く場合はエクスポネンシャルバックオフで再試行間隔を伸ばす。
    ただしサーバー不達中もキャッシュ設定で毎 POLL_INTERVAL ブロック判定を継続し、
    時刻によるブロック遷移（8:00 ブロック開始など）がサーバー障害に左右されないようにする。
    """
    consecutive_failures = 0
    MAX_BACKOFF = 600  # 最大10分
    # サーバー不達時のフォールバック。起動時キャッシュをセットしておくことで
    # polling_loop が初回サーバー到達前からでも時刻ブロック判定できる
    last_config = load_cached_config()
    next_server_retry = 0.0  # この time.time() 以降になったらサーバーを叩く

    while True:
        now = time.time()
        if now >= next_server_retry:
            try:
                response = requests.get(url, headers=_API_HEADERS, timeout=10)
                response.raise_for_status()
                if consecutive_failures > 0:
                    log(f"reconnected after {consecutive_failures} failure(s)")
                consecutive_failures = 0
                config = response.json()
                last_config = config
                save_cached_config(config)  # 次回起動用にキャッシュ更新
                next_server_retry = now + POLL_INTERVAL
            except Exception as e:
                consecutive_failures += 1
                # 最初の失敗と5の倍数回目だけログに残してスパムを防ぐ
                if consecutive_failures == 1 or consecutive_failures % 5 == 0:
                    log(f"error (#{consecutive_failures}): {e}")
                # 30s → 60s → 120s → 240s → 最大600s でバックオフ
                backoff = min(POLL_INTERVAL * (2 ** min(consecutive_failures - 1, 4)), MAX_BACKOFF)
                next_server_retry = time.time() + backoff

        # サーバー不達中もキャッシュ設定で毎 POLL_INTERVAL ブロック判定を継続する
        if last_config is not None:
            yield last_config
        time.sleep(POLL_INTERVAL)


def apply_config(config, current_state, last_version, log, lang="ja"):
    """
    ローカル判定のみ。サーバーの時刻・状態に依存しない。
    戻り値: (new_state, new_last_version, events)
    """
    s              = STRINGS[lang]
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
            log(s["log_emergency"])
            events.append("emergency_unblock")
        return False, version, events

    should_block = should_be_blocked(config)

    if should_block:
        if config_changed or current_state is not True:
            block(config.get("sites", []), config.get("apps", []))
        # False → True の遷移のみ記録（起動時の None → True は記録しない）
        if current_state is False:
            log(s["log_block"])
            events.append("block_start")
        return True, version, events
    else:
        if current_state is not False:
            unblock(log)
        # True → False の遷移のみ記録（起動時の None → False は記録しない）
        if current_state is True:
            log(s["log_unblock"])
            events.append("block_end")
        return False, version, events


def main():
    if not is_admin():
        relaunch_as_admin()
        return

    # 固定インストールパスへの移動（初回起動時のみ実行。以後はINSTALL_PATHから起動される）
    ensure_installed()

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
    lang      = local.get("lang", "ja")
    s         = STRINGS[lang]

    LOG_FILE = os.path.join(CONFIG_DIR, "agent.log")

    def log(msg):
        now = datetime.datetime.now(JST).strftime("%H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {msg}\n")

    # ログにはトークン末尾8文字のみ記録してフルトークンの漏洩を防ぐ
    masked_url = cloud_url[:-8] + "..." + cloud_url[-4:] if len(cloud_url) > 12 else "***"
    log(f"start URL={masked_url} exe={sys.executable}")

    # register_autostartはブロック開始を遅らせないようバックグラウンドで実行する
    def _reregister():
        register_autostart()
        log("autostart re-registered")
    threading.Thread(target=_reregister, daemon=True).start()

    # config URLからlog・アプリ追加のURLを導出する（トークンは共通）
    log_url = cloud_url.replace("/api/config/", "/api/log/")
    add_url = cloud_url.replace("/api/config/", "/api/apps/add/")

    # トレイアイコンをメインスレッドで動かすため、ポーリングループを別スレッドに移す
    tray = setup_tray(add_url, log, lang)

    # 前回取得した設定をキャッシュから即時適用（ネットワーク待ちゼロ）
    cached = load_cached_config()
    if cached:
        log("applying cached config on startup")
        init_state, init_version, _ = apply_config(cached, None, None, log, lang)
        _tray_state["blocking"] = init_state
        tray.icon = make_icon(init_state is True)
        log(f"cache applied: {'blocking' if init_state else 'unblocked'}")
    else:
        init_state, init_version = None, None

    def polling_loop():
        current_state  = init_state
        last_version   = init_version
        prev_emergency = False  # 前回pollで緊急解除中だったか
        for config in config_stream(cloud_url, log):
            # 自動更新チェック（サーバーが新バージョンを通知した場合のみ実行）
            latest_ver = config.get("agent_version", "")
            dl_url     = config.get("agent_download_url", "")
            if latest_ver and dl_url and _is_newer(latest_ver, VERSION):
                log(f"update available: {VERSION} → {latest_ver}")
                apply_update(dl_url, log)
                return  # apply_update が sys.exit するが念のため
            emergency = config.get("emergency_unblock", False)
            current_state, last_version, events = apply_config(
                config, current_state, last_version, log, lang
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
                    # EdgeのNetworkServiceを再起動してDNSキャッシュをクリアする
                    # DoHポリシーの即時反映とhostsファイルの即時適用のために毎回実行する
                    kill_edge_connections(log)
                    if prev_emergency:
                        notify(s["notify_reblock"])
                    else:
                        notify(s["notify_block_start"].format(comment=get_comment("block_start", lang)))
                elif event == "block_end":
                    notify(s["notify_block_end"].format(comment=get_comment("block_end", lang)))
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
    try:
        main()
    except Exception as e:
        import traceback
        log_path = os.path.join(os.environ.get("APPDATA", ""), "Toxov", "agent.log")
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[CRASH] {e}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        raise
