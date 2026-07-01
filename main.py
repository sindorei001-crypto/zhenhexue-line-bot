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
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction="""你是「真熱血AI助理」，真熱血整合行銷有限公司的內部AI助手。
你服務的對象是公司創辦人江彥霆。

你的職責：
- 協助處理行銷策略、提案、文件
- 回答業務、專案相關問題
- 提供快速、精準、直接的建議

回覆風格：
- 簡潔有力，不廢話
- 用繁體中文
- 必要時提供結構化清單
- 收到圖片/影片/檔案時，描述你觀察到的內容並詢問需要什麼協助"""
)

LINE_API = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_API = "https://api-data.line.me/v2/bot/message/{message_id}/content"


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


async def ask_gemini(user_message: str) -> str:
    response = model.generate_content(user_message)
    return response.text


async def ask_gemini_with_image(image_bytes: bytes, content_type: str) -> str:
    import PIL.Image
    import io
    image = PIL.Image.open(io.BytesIO(image_bytes))
    response = model.generate_content(["請描述這張圖片，並問我需要什麼協助。", image])
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
                reply_text = await ask_gemini(user_text)
                await reply_message(reply_token, reply_text)

            elif message_type == "image":
                image_bytes = await get_line_content(message_id)
                reply_text = await ask_gemini_with_image(image_bytes, "image/jpeg")
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
