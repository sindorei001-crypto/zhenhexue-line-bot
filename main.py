import os
import hashlib
import hmac
import base64
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import google.generativeai as genai

app = FastAPI()

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

genai.configure(api_key=GEMINI_API_KEY)

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

HELP_TEXT = """🤖 真熱血AI助理 指令清單

直接傳訊息 → AI 助理回覆

呼叫團隊成員：
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
