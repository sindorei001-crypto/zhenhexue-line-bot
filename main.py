import os
import hashlib
import hmac
import base64
import httpx
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GOOGLE_CALENDAR_CREDENTIALS = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

genai.configure(api_key=GEMINI_API_KEY)

TW = ZoneInfo("Asia/Taipei")

def get_calendar_service():
    creds_dict = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)

LINE_API = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_API = "https://api-data.line.me/v2/bot/message/{message_id}/content"

AGENTS = {
    "rex": {
        "name": "Rex｜幕僚長",
        "prompt": """你現在扮演 Rex，真熱血整合行銷有限公司的幕僚長與總指揮。
你的風格：直接、有優先順序、不廢話。
你的職責：接收創辦人今日狀況，輸出 CEO Briefing，包含：
- 今日最重要3件事
- 正在燒但還沒處理的事
- 今天該派給誰什麼任務
- 本週風險
- Rex 一句話建議
用繁體中文，500字以內。"""
    },
    "kai": {
        "name": "Kai｜專案經理",
        "prompt": """你現在扮演 Kai，真熱血整合行銷有限公司的專案經理。
你的風格：條理清晰、任務導向、重視截止日。
你的職責：把創辦人說的需求拆解成具體任務清單，每個任務包含：
- 任務名稱
- 負責人建議
- 截止日建議
- 優先順序（高/中/低）
用繁體中文，結構化輸出。"""
    },
    "vera": {
        "name": "Vera｜財務行政",
        "prompt": """你現在扮演 Vera，真熱血整合行銷有限公司的財務行政。
你的風格：精準、數字導向、提醒風險。
你的職責：協助處理財務相關問題，包含損益分析、應收款追蹤、毛利試算、費用建議。
用繁體中文，數字要清楚列出。"""
    },
    "muse": {
        "name": "Muse｜生產總監",
        "prompt": """你現在扮演 Muse，真熱血整合行銷有限公司的生產總監。
你的風格：創意與執行並重，直接產出內容初稿。
你的職責：根據創辦人的需求，直接產出文案、貼文、新聞稿、提案摘要等交付物初稿。
用繁體中文，直接給內容，不要說「我會幫你寫」。"""
    },
    "felix": {
        "name": "Felix｜提案報價",
        "prompt": """你現在扮演 Felix，真熱血整合行銷有限公司的提案報價專員。
你的風格：精準診斷需求，給出三個方案選項。
你的職責：根據客戶需求，提出基本/標準/進階三方案，每個方案包含服務範圍、報價區間、毛利估算。
用繁體中文，表格或清單格式輸出。"""
    },
    "nova": {
        "name": "Nova｜研考專員",
        "prompt": """你現在扮演 Nova，真熱血整合行銷有限公司的研考專員。
你的風格：嚴謹、來源導向、區分已確認與待補資料。
你的職責：協助政策研究、產業分析、數據查核，並標示資料來源可信度。
用繁體中文，每個資訊標示 ✅已確認 / ⚠️待補 / ❌缺口。"""
    },
    "maya": {
        "name": "Maya｜社群小編",
        "prompt": """你現在扮演 Maya，真熱血整合行銷有限公司的社群小編。
你的風格：接地氣、有梗、懂演算法。
你的職責：根據主題產出 FB/IG/Threads 貼文，包含內文、hashtag、最佳發文時間建議。
用繁體中文，直接給貼文內容，不要說「建議你這樣寫」。"""
    },
}

def get_agent_model(system_prompt: str) -> genai.GenerativeModel:
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=system_prompt
    )

DEFAULT_MODEL = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction="""你是「小萱」，真熱血整合行銷有限公司的AI秘書助理。
你服務的對象是公司負責人江彥霆，稱呼他為「江總」。

你的職責：
- 協助處理行銷策略、提案、文件
- 回答業務、專案相關問題
- 提供快速、精準、直接的建議

回覆風格：
- 簡潔有力，不廢話
- 用繁體中文
- 必要時提供結構化清單"""
)

HELP_TEXT = """🤖 熱血助理-小萱 指令清單

直接傳訊息 → 小萱 AI 回覆

📅 日曆管理：
/cal 今天 → 查今日行程
/cal 明天 → 查明日行程
/cal 本週 → 查本週行程
/cal 明天下午三點跟王董開會 → 自然語言新增行程
/cal 刪除 今天 會議名稱 → 刪除行程

🤖 呼叫團隊成員：
/rex [今日狀況] → Rex 幕僚長 briefing
/kai [任務描述] → Kai 拆解任務清單
/vera [財務問題] → Vera 財務分析
/muse [內容需求] → Muse 產出初稿
/felix [客戶需求] → Felix 三方案報價
/nova [研究主題] → Nova 資料查核
/maya [貼文主題] → Maya 社群貼文

/help → 顯示此清單"""


def verify_signature(body: bytes, signature: str) -> bool:
    hash_value = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_value).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def get_line_content(message_id: str) -> bytes:
    url = LINE_CONTENT_API.format(message_id=message_id)
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        return resp.content


async def reply_message(reply_token: str, text: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    async with httpx.AsyncClient() as client:
        await client.post(LINE_API, headers=headers, json=payload)


async def process_text(text: str) -> str:
    text = text.strip()

    if text.lower() == "/help":
        return HELP_TEXT

    if text.lower().startswith("/cal"):
        cal_input = text[4:].strip()
        return await handle_calendar(cal_input)

    for cmd, agent in AGENTS.items():
        if text.lower().startswith(f"/{cmd}"):
            user_input = text[len(cmd)+1:].strip()
            if not user_input:
                return f"請告訴 {agent['name']} 你的需求。\n例如：/{cmd} [你的問題或任務]"
            m = get_agent_model(agent["prompt"])
            response = m.generate_content(user_input)
            return f"── {agent['name']} ──\n\n{response.text}"

    response = DEFAULT_MODEL.generate_content(text)
    return response.text


NL_CALENDAR_MODEL = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=f"""你是行程解析助手。今天是 {datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d')}，星期{['一','二','三','四','五','六','日'][datetime.now(ZoneInfo('Asia/Taipei')).weekday()]}。
使用者會用自然語言描述要新增的行程，你需要解析出：
- date: YYYY/MM/DD 格式
- time: HH:MM 格式（24小時制）
- duration: 小時數（預設1）
- title: 行程標題

只回傳 JSON，不要任何說明。格式：
{{"date":"2026/07/02","time":"14:00","duration":1,"title":"跟王董開會"}}

如果無法解析，回傳：{{"error":"無法解析"}}"""
)


async def handle_calendar(text: str) -> str:
    cmd = text.strip()

    if cmd in ["今天", "今日", "查詢", ""]:
        now = datetime.now(TW)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return await list_events(start, start + timedelta(days=1), "今天")

    if cmd.startswith("明天") or cmd.startswith("明日"):
        now = datetime.now(TW)
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return await list_events(start, start + timedelta(days=1), "明天")

    if cmd.startswith("本週") or cmd.startswith("這週"):
        now = datetime.now(TW)
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return await list_events(start, start + timedelta(days=7), "本週")

    if cmd.startswith("刪除") or cmd.startswith("取消"):
        return await delete_event(cmd[2:].strip())

    # 自然語言新增（含舊格式 /cal 新增 ...）
    query = cmd[2:].strip() if (cmd.startswith("新增") or cmd.startswith("加入")) else cmd
    return await add_event_nl(query)


async def list_events(start: datetime, end: datetime, label: str) -> str:
    try:
        service = get_calendar_service()
        result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            timeZone="Asia/Taipei"
        ).execute()
        items = result.get("items", [])
        if not items:
            return f"📅 {label}沒有行程。"
        lines = [f"📅 {label}行程（共 {len(items)} 項）：\n"]
        for i, ev in enumerate(items, 1):
            start_raw = ev["start"].get("dateTime", ev["start"].get("date", ""))
            if "T" in start_raw:
                dt = datetime.fromisoformat(start_raw).astimezone(TW)
                time_str = dt.strftime("%H:%M")
            else:
                time_str = "全天"
            lines.append(f"{i}. {time_str} {ev.get('summary', '（無標題）')}")
        return "\n".join(lines)
    except Exception as e:
        return f"查詢行程失敗：{e}"


async def add_event_nl(text: str) -> str:
    """用自然語言或固定格式新增行程"""
    try:
        # 先嘗試固定格式 YYYY/MM/DD HH:MM 標題
        parts = text.split(" ", 2)
        if len(parts) == 3 and "/" in parts[0] and ":" in parts[1]:
            date_str, time_str, title = parts
            dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y/%m/%d %H:%M").replace(tzinfo=TW)
            duration = 1
        else:
            # 自然語言解析
            resp = NL_CALENDAR_MODEL.generate_content(text)
            raw = resp.text.strip().strip("```json").strip("```").strip()
            parsed = json.loads(raw)
            if "error" in parsed:
                return f"無法理解行程內容，請試試：\n/cal 明天下午三點跟王董開會\n/cal 2026/07/02 15:00 跟王董開會"
            dt_start = datetime.strptime(f"{parsed['date']} {parsed['time']}", "%Y/%m/%d %H:%M").replace(tzinfo=TW)
            title = parsed["title"]
            duration = parsed.get("duration", 1)

        dt_end = dt_start + timedelta(hours=duration)
        service = get_calendar_service()
        event = {
            "summary": title,
            "start": {"dateTime": dt_start.isoformat(), "timeZone": "Asia/Taipei"},
            "end": {"dateTime": dt_end.isoformat(), "timeZone": "Asia/Taipei"},
        }
        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        return f"✅ 已新增行程：\n📌 {title}\n🕐 {dt_start.strftime('%m/%d（%A）%H:%M')}，共 {duration} 小時"
    except json.JSONDecodeError:
        return "解析失敗，請重新描述行程。"
    except ValueError:
        return "日期格式錯誤，請試試：/cal 明天下午兩點開會"
    except Exception as e:
        return f"新增行程失敗：{e}"


async def delete_event(text: str) -> str:
    """刪除行程：解析日期和關鍵字"""
    try:
        now = datetime.now(TW)
        if "今天" in text or "今日" in text:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif "明天" in text or "明日" in text:
            start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        service = get_calendar_service()
        result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        items = result.get("items", [])
        if not items:
            return "找不到可刪除的行程。"

        # 關鍵字比對
        keyword = text.replace("今天", "").replace("明天", "").replace("今日", "").replace("明日", "").strip()
        if keyword:
            matched = [ev for ev in items if keyword in ev.get("summary", "")]
            if not matched:
                lines = ["找不到包含「{}」的行程，當天行程：".format(keyword)]
                for ev in items:
                    lines.append(f"• {ev.get('summary', '無標題')}")
                return "\n".join(lines)
            if len(matched) == 1:
                service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=matched[0]["id"]).execute()
                return f"✅ 已刪除：{matched[0].get('summary', '無標題')}"
            lines = ["找到多筆符合行程，請更精確說明："]
            for ev in matched:
                lines.append(f"• {ev.get('summary', '無標題')}")
            return "\n".join(lines)

        if len(items) == 1:
            service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=items[0]["id"]).execute()
            return f"✅ 已刪除：{items[0].get('summary', '無標題')}"

        lines = ["當天有多筆行程，請指定關鍵字：\n/cal 刪除 今天 會議名稱\n\n當天行程："]
        for ev in items:
            lines.append(f"• {ev.get('summary', '無標題')}")
        return "\n".join(lines)
    except Exception as e:
        return f"刪除失敗：{e}"


async def ask_gemini_with_image(image_bytes: bytes) -> str:
    import PIL.Image
    import io
    image = PIL.Image.open(io.BytesIO(image_bytes))
    response = DEFAULT_MODEL.generate_content(["請描述這張圖片的內容，並問我需要什麼協助。", image])
    return response.text


@app.get("/")
async def health():
    return {"status": "真熱血AI助理 online"}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    data = json.loads(body)

    for event in data.get("events", []):
        if event.get("type") != "message":
            continue

        reply_token = event.get("replyToken")
        message = event.get("message", {})
        message_type = message.get("type")
        message_id = message.get("id")

        try:
            if message_type == "text":
                user_text = message.get("text", "")
                reply_text = await process_text(user_text)
                await reply_message(reply_token, reply_text)

            elif message_type == "image":
                image_bytes = await get_line_content(message_id)
                reply_text = await ask_gemini_with_image(image_bytes)
                await reply_message(reply_token, reply_text)

            elif message_type == "video":
                await reply_message(reply_token, "收到影片了。請問這支影片需要什麼分析或協助？")

            elif message_type == "file":
                file_name = message.get("fileName", "檔案")
                file_size = message.get("fileSize", 0)
                size_kb = round(file_size / 1024, 1)
                await reply_message(
                    reply_token,
                    f"收到檔案：{file_name}（{size_kb} KB）\n請問這個檔案需要什麼協助？"
                )

            elif message_type == "audio":
                await reply_message(reply_token, "收到語音訊息了。目前語音轉文字功能建置中，請改用文字傳達需求。")

            else:
                await reply_message(reply_token, f"收到（{message_type}）。請問需要什麼協助？")

        except Exception as e:
            print(f"Error processing event: {e}")
            try:
                await reply_message(reply_token, "處理訊息時發生錯誤，請稍後再試。")
            except Exception:
                pass

    return JSONResponse(content={"status": "ok"})
