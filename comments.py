import random

COMMENTS = {
    "ja": {
        'initial': [
            "記録が始まりました",
            "最初の積み上げです",
            "継続の起点です",
            "習慣の形成が始まっています",
            "まだ初期段階ですが、記録されています",
            "最初の数日が最も重要です",
            "記録は今ここから始まっています",
            "出発点が刻まれています",
        ],
        'ongoing': [
            "習慣として定着し始めています",
            "継続記録を更新中です",
            "一週間を超えました",
            "日常の一部になりつつあります",
            "ルーティンとして機能し始めています",
            "記録が着実に積み上がっています",
            "継続が確認されています",
            "安定した記録が続いています",
            "習慣の土台ができています",
            "このペースが維持されています",
            "積み上げが続いています",
            "継続が定着しつつあります",
            "記録は伸び続けています",
            "安定が続いています",
            "習慣が強化されています",
        ],
        'longterm': [
            "長期維持が続いています",
            "習慣として完全に定着しています",
            "この継続は珍しい部類です",
            "記録が長期に及んでいます",
            "長期記録が更新されました",
            "安定した継続が証明されています",
            "深く根付いた習慣です",
            "長期的な定着が確認されています",
            "継続記録の更新が続いています",
            "この積み上げは本物です",
            "習慣が完全に機能しています",
            "長期継続が記録されています",
            "安定が長く続いています",
            "継続の質が高い水準で維持されています",
            "このレベルの継続は少数です",
            "積み上げが長期に及んでいます",
            "記録は更新され続けています",
            "習慣が生活に組み込まれています",
            "長期的な自制が確認されています",
            "ここまでの記録は本物です",
        ],
        'comeback': [
            "継続はここから戻せます",
            "記録は中断されましたが、進行は残っています",
            "また今日から積み上げられます",
            "一度の解除で全ては失われません",
            "再び始まっています",
            "中断があっても、続ける意志は残っています",
            "ここから記録を作り直せます",
            "一度の失敗は全体を消しません",
        ],
        'block_start': [
            "今日のブロックが始まりました",
            "スケジュールどおりです",
            "今日のセッションが開始されました",
            "ブロック時間に入りました",
            "今日の記録が始まります",
        ],
        'block_end': [
            "今日の記録が完了しました",
            "セッションが終了しました",
            "スケジュールどおり終了しました",
            "今日の分が積み上がりました",
            "記録に残りました",
        ],
    },
    "en": {
        'initial': [
            "Your streak has begun.",
            "First step logged.",
            "The habit is taking shape.",
            "The first few days matter most.",
            "Your record starts here.",
            "Day one is in the books.",
            "Building from the ground up.",
            "Every streak starts somewhere.",
        ],
        'ongoing': [
            "The habit is taking hold.",
            "Streak updated.",
            "Past the one-week mark.",
            "Becoming part of your routine.",
            "Consistent progress recorded.",
            "The streak keeps climbing.",
            "Steady record continuing.",
            "The foundation is solid.",
            "This pace is holding.",
            "Momentum is building.",
            "Consistency confirmed.",
            "The habit is strengthening.",
            "Your record keeps growing.",
            "Stability maintained.",
            "Routine is working.",
        ],
        'longterm': [
            "Long-term consistency recorded.",
            "Fully established habit.",
            "This level of commitment is rare.",
            "An extended streak confirmed.",
            "Long-term record updated.",
            "Consistent discipline proven.",
            "Deeply rooted habit.",
            "Long-term consistency verified.",
            "The streak keeps setting new records.",
            "This commitment is real.",
            "The habit is fully functioning.",
            "Sustained over the long run.",
            "Stability has lasted.",
            "High-level consistency maintained.",
            "Very few reach this streak.",
            "An extended run of commitment.",
            "The record keeps updating.",
            "Built into your lifestyle.",
            "Long-term self-control confirmed.",
            "This record is the real thing.",
        ],
        'comeback': [
            "You can rebuild from here.",
            "Progress wasn't lost — only paused.",
            "Today is a fresh start.",
            "One slip doesn't erase the effort.",
            "Starting again.",
            "The will to continue is still there.",
            "You can rewrite the record from here.",
            "One failure doesn't cancel everything.",
        ],
        'block_start': [
            "Today's block has started.",
            "Right on schedule.",
            "Today's session is underway.",
            "Block time has begun.",
            "Today's record is starting.",
        ],
        'block_end': [
            "Today's session is complete.",
            "Block ended successfully.",
            "Finished on schedule.",
            "Today's streak is secured.",
            "Logged and recorded.",
        ],
    },
}


def get_phase(streak: int, has_emergency_history: bool) -> str:
    if has_emergency_history and streak <= 6:
        return 'comeback'
    if streak <= 6:
        return 'initial'
    if streak <= 29:
        return 'ongoing'
    return 'longterm'


def get_comment(phase: str, lang: str = "ja") -> str:
    lang_comments = COMMENTS.get(lang, COMMENTS["ja"])
    pool = lang_comments.get(phase, lang_comments['ongoing'])
    return random.choice(pool)
