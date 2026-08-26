#!/usr/bin/env python3
"""
The Row 補貨監控（GitHub Actions 版）。

跑在雲端，和你的 Mac 睡不睡無關。
偵測到補貨時：
  1. 開一個 GitHub Issue → GitHub 自動寄 email 給你（不需要任何密碼）
  2. 若有設定 SMTP secrets，額外直接寄一封信

狀態存在 state.json，由 workflow commit 回 repo。
"""

import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(BASE, "products.txt")
STATE_FILE = os.path.join(BASE, "state.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20
RETRIES = 3


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC  {msg}", flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_products():
    urls = []
    with open(PRODUCTS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line.split("?")[0].rstrip("/"))
    return urls


def fetch(url):
    last = None
    ctx = ssl.create_default_context()
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json,text/javascript,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                      # noqa: BLE001
            last = e
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    raise last


def parse(data):
    variants = [{
        "title": v.get("title"),
        "available": bool(v.get("available")),
    } for v in data.get("variants", [])]
    return {
        "title": data.get("title", "?"),
        "available": bool(data.get("available")) or any(v["available"] for v in variants),
        "variants": variants,
        "price": (data.get("price") or 0) / 100.0,
    }


# ---------- 通知管道 1：GitHub Issue（不需要任何密碼）----------

def create_issue(title, body):
    token = env("GITHUB_TOKEN")
    repo = env("GITHUB_REPOSITORY")
    if not token or not repo:
        log("  (沒有 GITHUB_TOKEN/GITHUB_REPOSITORY，跳過建立 Issue)")
        return False

    owner = repo.split("/")[0]
    payload = json.dumps({
        "title": title,
        "body": body,
        "assignees": [owner],      # 指派給自己，確保觸發通知信
        "labels": ["restock"],
    }).encode()

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "therow-watch",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            num = json.loads(r.read().decode())["number"]
            log(f"  >>> 已建立 Issue #{num}（GitHub 會寄通知信給你）")
            return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        log(f"  (建立 Issue 失敗 HTTP {e.code}: {detail})")
        # assignees/labels 可能因權限失敗，退回最簡形式再試一次
        try:
            req2 = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/issues",
                data=json.dumps({"title": title, "body": body}).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "therow-watch",
                },
            )
            with urllib.request.urlopen(req2, timeout=30) as r:
                num = json.loads(r.read().decode())["number"]
                log(f"  >>> 已建立 Issue #{num}（簡化模式）")
                return True
        except Exception as e2:                     # noqa: BLE001
            log(f"  (簡化模式也失敗: {e2})")
    except Exception as e:                          # noqa: BLE001
        log(f"  (建立 Issue 失敗: {type(e).__name__}: {e})")
    return False


# ---------- 通知管道 2：SMTP（可選，要設 secrets）----------

def env(name, default=""):
    """讀環境變數。GitHub 對「未設定的 secret」會給空字串而不是不設定，
    所以空字串必須當成沒有，否則預設值永遠不會生效。"""
    return (os.environ.get(name) or "").strip() or default


def send_email(subject, text_body, html_body=None):
    # 先確認有帳密再解析其他設定，避免沒設定時還去 int() 空字串
    user = env("SMTP_USER")
    pw = env("SMTP_PASSWORD")
    if not user or not pw:
        log("  (未設定 SMTP secrets，略過直接寄信 — Issue 通知不受影響)")
        return False

    host = env("SMTP_HOST", "smtp.gmail.com")
    try:
        port = int(env("SMTP_PORT", "587"))
    except ValueError:
        log(f"  (SMTP_PORT 不是數字: {env('SMTP_PORT')!r}，改用 587)")
        port = 587
    to = env("MAIL_TO", user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                s.login(user, pw); s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo(); s.starttls(context=ctx); s.ehlo()
                s.login(user, pw); s.send_message(msg)
        log(f"  >>> 已寄出 email 給 {to}")
        return True
    except Exception as e:                          # noqa: BLE001
        log(f"  (寄信失敗: {type(e).__name__}: {e})")
        return False


def notify_all(subject, text, html=None):
    """送出所有通知管道。任何一個炸掉都不影響其他管道與庫存檢查本身。"""
    ok = False
    for fn, args in ((create_issue, (subject, text)), (send_email, (subject, text, html))):
        try:
            ok = fn(*args) or ok
        except Exception as e:                      # noqa: BLE001
            log(f"  (通知管道 {fn.__name__} 例外: {type(e).__name__}: {e})")
    return ok


def build_message(info, url, sizes):
    price = f"${info['price']:,.0f}"
    sz = ", ".join(sizes) if sizes else "One Size"
    ts = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    subject = f"🔔 補貨了：{info['title']} ({price})"
    text = (f"{info['title']}\n價格：{price}\n有貨：{sz}\n偵測時間：{ts}\n\n"
            f"立刻前往：{url}\n\n熱門款可能幾分鐘內再次售完，建議馬上結帳。\n")
    html = (f'<h2>🔔 補貨了</h2><p style="font-size:18px"><b>{info["title"]}</b></p>'
            f'<p>價格：<b>{price}</b><br>有貨：{sz}<br>偵測時間：{ts}</p>'
            f'<p><a href="{url}" style="background:#111;color:#fff;padding:12px 22px;'
            f'text-decoration:none;border-radius:4px;display:inline-block">前往商品頁</a></p>')
    return subject, text, html


def main():
    if "--test" in sys.argv:
        s, t, h = build_message(
            {"title": "測試：Margaux Shoulder 12 Bag", "price": 4900.0},
            "https://www.therow.com/products/margaux-shoulder-12-black",
            ["Black / One Size"])
        t = "這是一封測試通知，用來確認管道暢通。\n\n" + t
        ok = notify_all("✅ 測試通知（可直接關閉）", t, h)
        print("::notice::測試通知已送出" if ok else "::warning::沒有任何通知管道成功")
        return 0

    state = load_state()
    changed = False

    for url in read_products():
        try:
            info = parse(fetch(url + ".js"))
        except Exception as e:                      # noqa: BLE001
            log(f"[錯誤] {url} -> {type(e).__name__}: {e}")
            continue

        prev = state.get(url, {}).get("available")
        now = info["available"]
        sizes = [v["title"] for v in info["variants"] if v["available"]]
        log(f"{'有貨 ✅' if now else '缺貨 ❌'}  {info['title']}  "
            f"${info['price']:,.0f}{(' [' + ', '.join(sizes) + ']') if sizes else ''}")

        if now and prev is not True:
            subject, text, html = build_message(info, url, sizes)
            notify_all(subject, text, html)
        elif not now and prev is True:
            log("  >>> 又賣完了")

        if prev != now:
            changed = True
        state[url] = {
            "available": now,
            "title": info["title"],
            "price": info["price"],
            "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_in_stock": (datetime.now(timezone.utc).isoformat(timespec="seconds")
                              if now else state.get(url, {}).get("last_in_stock")),
        }

    save_state(state)
    log(f"狀態{'有變化，將 commit' if changed else '未變化'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
