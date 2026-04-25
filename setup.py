import subprocess
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
python = sys.executable
scheduler = os.path.join(BASE_DIR, "scheduler.py")

ps = f"""
$action = New-ScheduledTaskAction -Execute '{python}' -Argument '"{scheduler}"'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId '{os.environ["USERNAME"]}' -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'FreedomScheduler' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
"""

result = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)

if result.returncode == 0:
    print("登録完了！次回ログイン時から自動起動します")
    print("今すぐ手動で起動する場合: python scheduler.py")
else:
    print("エラーが発生しました:")
    print(result.stderr)
