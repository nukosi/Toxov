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


def send_contact_email(name: str, from_email: str, message: str, lang: str = "ja") -> bool:
    """お問い合わせフォームの内容を toxovmail@gmail.com に転送する。"""
    if not RESEND_API_KEY:
        return False
    name_label    = "名前" if lang == "ja" else "Name"
    email_label   = "メールアドレス" if lang == "ja" else "Email"
    message_label = "内容" if lang == "ja" else "Message"
    subject = f"[Toxov お問い合わせ] {name or from_email}" if lang == "ja" else f"[Toxov Contact] {name or from_email}"
    import html as _html
    safe_name    = _html.escape(name or "—")
    safe_email   = _html.escape(from_email)
    safe_message = _html.escape(message).replace("\n", "<br>")
    payload = {
        "from":     FROM_EMAIL,
        "to":       ["toxovmail@gmail.com"],
        "reply_to": from_email,
        "subject":  subject,
        "html": f"""
<div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#1a1a1a">
  <h2 style="margin-bottom:20px">お問い合わせ / Contact</h2>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:8px 16px 8px 0;color:#555;white-space:nowrap"><strong>{name_label}</strong></td>
        <td style="padding:8px 0">{safe_name}</td></tr>
    <tr><td style="padding:8px 16px 8px 0;color:#555;white-space:nowrap"><strong>{email_label}</strong></td>
        <td style="padding:8px 0">{safe_email}</td></tr>
  </table>
  <hr style="margin:16px 0;border:none;border-top:1px solid #eee">
  <p style="color:#555;margin-bottom:8px"><strong>{message_label}</strong></p>
  <p style="line-height:1.7">{safe_message}</p>
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
