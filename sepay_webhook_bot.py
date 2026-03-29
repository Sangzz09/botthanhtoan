import asyncio
import logging
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI, Request
import uvicorn

# ─────────────────────────────────────────────
#  CẤU HÌNH
# ─────────────────────────────────────────────
BOT_TOKEN  = "8293331183:AAFbaUlOIms2ioHPgUpEF78q8zkPWTXnBvA" # Lấy token mới thay vào đây nhé
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher()
app = FastAPI()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

# Lưu trữ VIP tạm thời (Nên thay bằng Database SQLite/MySQL sau này)
valid_keys: dict[int, dict] = {}

# ─────────────────────────────────────────────
#  1. LOGIC CỦA BOT TELEGRAM
# ─────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message):
    uid = msg.from_user.id
    
    # Kiểm tra trạng thái VIP
    is_vip = False
    exp_str = "Chưa kích hoạt"
    if uid in valid_keys and valid_keys[uid]["expires"] > datetime.now():
        is_vip = True
        exp_str = valid_keys[uid]["expires"].strftime("%d/%m/%Y %H:%M")

    status = "✅ <b>Đã kích hoạt</b>" if is_vip else "❌ <b>Chưa kích hoạt</b>"
    
    welcome_msg = (
        "╔══════════════════════════╗\n"
        "║   🏆  <b>VIP PREDICT BOT</b>  🏆   ║\n"
        "╚══════════════════════════╝\n\n"
        f"👤 ID của bạn: <code>{uid}</code>\n"
        f"🔐 Trạng thái: {status}\n"
        f"⏳ Hạn dùng: <i>{exp_str}</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>HƯỚNG DẪN MUA VIP TỰ ĐỘNG 24/7</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Chuyển khoản theo cú pháp chính xác sau:\n"
        f"👉 Nội dung CK: <code>VIP {uid}</code>\n\n"
        "Bảng giá:\n"
        "• 30.000đ  = 1 Ngày\n"
        "• 120.000đ = 7 Ngày\n"
        "• 220.000đ = 30 Ngày\n\n"
        "<i>Hệ thống tự động mở khóa trong 3-5 giây sau khi nhận tiền!</i>"
    )
    await msg.answer(welcome_msg)

# ─────────────────────────────────────────────
#  2. LOGIC NHẬN WEBHOOK TỪ SEPAY
# ─────────────────────────────────────────────
@app.post("/sepay-webhook")
async def sepay_webhook(request: Request):
    try:
        data = await request.json()
        amount = int(data.get('transferAmount', 0))
        content = str(data.get('content', '')).upper()
        
        logging.info(f"💰 SePay báo: +{amount}đ | Nội dung: {content}")

        # Tìm chữ "VIP <ID>" trong nội dung chuyển khoản
        match = re.search(r'VIP\s*(\d+)', content)
        if match:
            user_id = int(match.group(1))
            days = 0
            
            # Tính số ngày dựa vào số tiền (Có thể tuỳ chỉnh lại)
            if amount >= 220000: days = 30
            elif amount >= 120000: days = 7
            elif amount >= 30000: days = 1
            
            if days > 0:
                # Cộng ngày sử dụng
                if user_id in valid_keys and valid_keys[user_id]["expires"] > datetime.now():
                    valid_keys[user_id]["expires"] += timedelta(days=days)
                else:
                    valid_keys[user_id] = {"expires": datetime.now() + timedelta(days=days)}
                
                exp_date = valid_keys[user_id]["expires"].strftime("%d/%m/%Y %H:%M")
                
                # Bắn tin nhắn qua Bot cho người dùng
                success_msg = (
                    f"🎉 <b>NẠP VIP THÀNH CÔNG!</b>\n\n"
                    f"💰 Đã nhận: +{amount:,}đ\n"
                    f"⏱ Gói: {days} ngày\n"
                    f"⏳ Hạn dùng mới: <b>{exp_date}</b>\n\n"
                    f"Cảm ơn bạn đã sử dụng dịch vụ!"
                )
                await bot.send_message(user_id, success_msg)
                logging.info(f"✅ Đã cấp VIP cho user {user_id} - {days} ngày")
                return {"status": "success", "message": "Đã cấp VIP"}
            else:
                logging.info("❌ Số tiền không đủ mốc mua VIP.")
        
        return {"status": "ignored", "message": "Không đúng cú pháp hoặc thiếu tiền"}
    
    except Exception as e:
        logging.error(f"Lỗi xử lý webhook: {e}")
        return {"status": "error"}

# ─────────────────────────────────────────────
#  3. CHẠY SONG SONG CẢ 2 HỆ THỐNG
# ─────────────────────────────────────────────
async def main():
    logging.info("🚀 Khởi động Bot & Webhook Server...")
    # Cấu hình Web Server chạy ở cổng 8000
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    
    # Chạy song song Server và Polling của Telegram
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())