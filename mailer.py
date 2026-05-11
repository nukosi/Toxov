import os
import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# ドメイン取得後は MAIL_FROM 環境変数を "noreply@toxov.app" に変更する
FROM_EMAIL = os.environ.get("MAIL_FROM", "onboarding@resend.dev")


def send_otp_email(to_email: str, code: str) -> bool:
    """管理者ログイン2FA用のOTPメールをResend経由で送信する。"""
    if not RESEND_API_KEY:
        return False
    payload = {
        "from":    FROM_EMAIL,
        "to":      [to_email],
        "subject": f"Toxov 管理者ログイン認証コード: {code}",
        "html": f"""
<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a1a1a">
  <h2 style="margin-bottom:16px">管理者ログイン認証</h2>
  <p style="color:#555;margin-bottom:24px">以下の認証コードを入力してください。有効期限は<strong>10分</strong>です。</p>
  <div style="font-size:2.5rem;font-weight:700;letter-spacing:10px;text-align:center;
              padding:24px;background:#f5f5f5;border-radius:8px;margin-bottom:24px;">{code}</div>
  <p style="color:#888;font-size:0.85rem;">このメールに心当たりがない場合は無視してください。</p>
</div>
""",
    }
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        return res.status_code in (200, 201)
    except Exception:
        return False


def send_password_reset(to_email: str, reset_url: str) -> bool:
    """パスワードリセットメールをResend経由で送信する。成功ならTrue。"""
    if not RESEND_API_KEY:
        return False
    payload = {
        "from":    FROM_EMAIL,
        "to":      [to_email],
        "subject": "Toxov パスワードリセット",
        "html": f"""
<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1a1a1a">
  <h2 style="margin-bottom:16px">パスワードリセット</h2>
  <p style="color:#555;margin-bottom:24px">
    以下のボタンをクリックしてパスワードをリセットしてください。<br>
    このリンクは<strong>1時間</strong>有効です。
  </p>
  <a href="{reset_url}"
     style="display:inline-block;padding:12px 28px;background:#4dabf7;color:#000;
            border-radius:8px;text-decoration:none;font-weight:600;font-size:1rem">
    パスワードをリセット
  </a>
  <p style="color:#888;font-size:0.85rem;margin-top:32px">
    このメールに心当たりがない場合は無視してください。<br>
    アカウントへの変更は行われません。
  </p>
</div>
""",
    }
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization":  f"Bearer {RESEND_API_KEY}",
                "Content-Type":   "application/json",
            },
            json=payload,
            timeout=10,
        )
        return res.status_code in (200, 201)
    except Exception:
        return False
