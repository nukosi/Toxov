from database import init_db, create_user

init_db()

print("nukosisnsblocker - ユーザー登録")
username = input("ユーザー名: ").strip()
password = input("パスワード: ").strip()

if not username or not password:
    print("ユーザー名とパスワードを入力してください")
else:
    create_user(username, password)
    print(f"ユーザー '{username}' を登録しました")
