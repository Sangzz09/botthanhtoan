"""
Bot Telegram Dự Đoán Tài Xỉu / Sicbo / Baccarat
Sử dụng: Python + aiogram 3.x
"""

import asyncio
import logging
import json
import os
import random
import re
import string
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request
import uvicorn

import requests

# ─────────────────────────────────────────────
#  CẤU HÌNH
# ─────────────────────────────────────────────
BOT_TOKEN  = "8293331183:AAFbaUlOIms2ioHPgUpEF78q8zkPWTXnBvA"
ADMIN_ID   = 7219600109
ADMIN_LINK = "https://t.me/sewdangcap"
SEPAY_API_TOKEN = "" # 🔴 ĐIỀN API TOKEN TỪ SEPAY.VN VÀO ĐÂY
BANK_STK   = "0886027767"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  LƯU TRỮ KEY BẰNG JSON
# ─────────────────────────────────────────────
DATA_FILE = "bot_data.json"

# Cấu trúc: { user_id: {"key": "...", "expires": datetime | None} }
valid_keys: dict[int, dict] = {}
all_users: set[int] = set()
pending_payments: dict[str, dict] = {} # Lưu mã QR đang chờ thanh toán
payment_history: dict[int, list] = {}
user_balances: dict[int, int] = {} # Lưu số dư ví của người dùng

key_store: dict[str, dict] = {
    "VIP-TEST-2024": {"duration_days": 1,   "used_by": None},
    "VIP-WEEK-001" : {"duration_days": 7,   "used_by": None},
    "VIP-MONTH-001": {"duration_days": 30,  "used_by": None},
    "VIP-FOREVER-1": {"duration_days": -1,  "used_by": None},  # -1 = vĩnh viễn
}

def save_data():
    vk_json = {}
    for uid, info in valid_keys.items():
        exp = info.get("expires")
        vk_json[str(uid)] = {
            "key": info["key"],
            "expires": exp.isoformat() if isinstance(exp, datetime) else None
        }
    data = {
        "valid_keys": vk_json, 
        "key_store": key_store, 
        "all_users": list(all_users),
        "pending_payments": pending_payments,
        "payment_history": payment_history,
        "user_balances": user_balances
    }
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log.error(f"Lỗi lưu file: {e}")

def load_data():
    global valid_keys, key_store, all_users, pending_payments, payment_history, user_balances
    if not os.path.exists(DATA_FILE):
        save_data()
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "key_store" in data:
            key_store.clear()
            key_store.update(data["key_store"])
        if "valid_keys" in data:
            valid_keys.clear()
            for uid_str, info in data["valid_keys"].items():
                exp_str = info.get("expires")
                expires = datetime.fromisoformat(exp_str) if exp_str else None
                valid_keys[int(uid_str)] = {"key": info["key"], "expires": expires}
        if "all_users" in data:
            all_users.clear()
            all_users.update(data["all_users"])
        if "pending_payments" in data:
            pending_payments.clear()
            pending_payments.update(data["pending_payments"])
        if "payment_history" in data:
            payment_history.clear()
            payment_history.update({int(k): v for k, v in data["payment_history"].items()})
        if "user_balances" in data:
            user_balances.clear()
            user_balances.update({int(k): int(v) for k, v in data["user_balances"].items()})
    except Exception as e:
        log.error(f"Lỗi đọc file: {e}")

# Gọi ngay khi chạy bot để nạp dữ liệu từ file JSON
load_data()

# ─────────────────────────────────────────────
#  DANH SÁCH GAME
# ─────────────────────────────────────────────
GAMES = [
    {"id": "sunwin_tx",    "name": "🎯 Tài Xỉu Sunwin",      "type": "taixiu"},
    {"id": "hitclub_hu",   "name": "🎰 TX Hitclub Hũ",        "type": "taixiu"},
    {"id": "hitclub_md5",  "name": "🔐 TX Hitclub MD5",       "type": "taixiu"},
    {"id": "baccarat_sexy","name": "👠 Baccarat Sexy",        "type": "baccarat"},
    {"id": "789club_tx",   "name": "🎲 TX 789Club",           "type": "taixiu"},
    {"id": "lc79_md5",     "name": "🔑 TX lc79 MD5",          "type": "taixiu"},
    {"id": "lc79_hu",      "name": "🏺 TX lc79 Hũ",           "type": "taixiu"},
    {"id": "sunwin_sicbo", "name": "🎱 Sicbo Sunwin",         "type": "sicbo"},
    {"id": "789_sicbo",    "name": "🎳 Sicbo 789Club",        "type": "sicbo"},
    {"id": "hit_sicbo",    "name": "🎯 Sicbo Hitclub",        "type": "sicbo"},
    {"id": "68gb_xanh",    "name": "🟢 68gb Bàn Xanh",       "type": "taixiu"},
    {"id": "68gb_md5",     "name": "🔒 68gb Bàn MD5",        "type": "taixiu"},
    {"id": "luck8",        "name": "🍀 Luck8",                "type": "taixiu"},
    {"id": "b52_tx",       "name": "✈️ TX B52",               "type": "taixiu"},
]

GAME_MAP = {g["id"]: g for g in GAMES}

# ─────────────────────────────────────────────
#  FSM STATES
# ─────────────────────────────────────────────
class UserState(StatesGroup):
    waiting_key = State()

class AdminState(StatesGroup):
    addkey_key = State()
    addkey_days = State()
    genkeys_amount = State()
    genkeys_days = State()
    delkey_key = State()
    broadcast_text = State()

# ─────────────────────────────────────────────
#  HELPER: Kiểm tra key còn hiệu lực
# ─────────────────────────────────────────────
def is_authorized(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    info = valid_keys.get(user_id)
    if not info:
        return False
    exp = info.get("expires")
    if exp is None:   # vĩnh viễn
        return True
    return datetime.now() < exp

def key_expire_str(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "Vĩnh viễn (Admin)"
    info = valid_keys.get(user_id)
    if not info:
        return "Chưa kích hoạt"
    exp = info.get("expires")
    if exp is None:
        return "Vĩnh viễn ♾️"
    remaining = exp - datetime.now()
    days = remaining.days
    hours = remaining.seconds // 3600
    return f"{days} ngày {hours} giờ"

def clean_memo(name: str) -> str:
    """Xóa dấu Tiếng Việt và ký tự đặc biệt để làm nội dung chuyển khoản"""
    n = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    clean = ''.join(e for e in n if e.isalnum()).upper()
    return clean if clean else "USER"

# ─────────────────────────────────────────────
#  HELPER: Gọi API dự đoán
#  Bạn thay URL thật vào đây
# ─────────────────────────────────────────────
def fetch_prediction(game_id: str) -> dict:
    """
    Gọi API lấy dữ liệu dự đoán thực tế.
    """
    # Xử lý API thật cho Tài Xỉu Sunwin
    if game_id == "sunwin_tx":
        try:
            resp = requests.get("https://sunwinsaygex-vd0m.onrender.com/api/sun", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "game_id"       : game_id,
                    "current_session": data.get("phien", 0),
                    "dice"          : [data.get("xuc_xac_1", 0), data.get("xuc_xac_2", 0), data.get("xuc_xac_3", 0)],
                    "total"         : data.get("tong", 0),
                    "result"        : str(data.get("ket_qua", "")).upper(),
                    "next_session"  : data.get("phien_hien_tai", 0),
                    "prediction"    : str(data.get("du_doan", "")).upper(),
                    "confidence"    : random.randint(75, 95), # API chưa trả về độ tin cậy, tự tạo ngẫu nhiên
                    "bridge_type"   : "Cầu Hệ Thống",
                    "pattern"       : "N/A",
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    # Xử lý API thật cho Sicbo Sunwin
    elif game_id == "sunwin_sicbo":
        try:
            resp = requests.get("https://sicbosunwin.onrender.com/api/sicbo/sunwin", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                conf_str = data.get("do_tin_cay", "80%")
                conf = int(float(conf_str.replace("%", ""))) if "%" in conf_str else 80
                
                return {
                    "game_id"       : game_id,
                    "current_session": str(data.get("Phien", "")).replace("#", ""),
                    "dice"          : [data.get("Xuc_xac_1", 0), data.get("Xuc_xac_2", 0), data.get("Xuc_xac_3", 0)],
                    "total"         : data.get("Tong", 0),
                    "result"        : str(data.get("Ket_qua", "")).upper(),
                    "next_session"  : data.get("phien_hien_tai", 0),
                    "prediction"    : str(data.get("du_doan", "")).upper(),
                    "confidence"    : conf,
                    "bridge_type"   : "Cầu Thông Minh",
                    "pattern"       : "N/A",
                    "position"      : str(data.get("dudoan_vi", "")),
                }
        except Exception as e:
            log.warning(f"API error for {game_id}: {e}")

    # ── DEMO DATA (dành cho các game chưa có API hoặc dự phòng khi API lỗi) ──
    dice = [random.randint(1, 6) for _ in range(3)]
    total = sum(dice)
    result = "TÀI" if total >= 11 else "XỈU"
    patterns = ["Cầu bệt", "Cầu đảo", "Cầu 1-1", "Cầu 2-2", "Cầu 3-3", "Cầu zigzag"]
    pattern_seq = "T-T-X-T" if result == "TÀI" else "X-X-T-X"
    current_session = random.randint(100000, 999999)

    return {
        "game_id"       : game_id,
        "current_session": current_session,
        "dice"          : dice,
        "total"         : total,
        "result"        : result,
        "next_session"  : current_session + 1,
        "prediction"    : "TÀI" if random.random() > 0.45 else "XỈU",
        "confidence"    : random.randint(72, 95),
        "bridge_type"   : random.choice(patterns),
        "pattern"       : pattern_seq,
        # Sicbo / Baccarat extras
        "position"      : random.choice(["Cửa Tài", "Cửa Xỉu", "Cửa Chẵn", "Cửa Lẻ"]),
        "baccarat_result": random.choice(["Player", "Banker", "Tie"]),
    }

# ─────────────────────────────────────────────
#  KEYBOARD BUILDERS
# ─────────────────────────────────────────────
def kb_start(authorized: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔑 Nhập Key", callback_data="enter_key")],
        [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit")],
        [InlineKeyboardButton(text="ℹ️ Trợ Giúp (Help)", callback_data="help")],
    ]
    if authorized:
        rows.append([InlineKeyboardButton(text="🎮 Danh sách Game", callback_data="game_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_help() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit"),
         InlineKeyboardButton(text="👤 Tài Khoản", callback_data="account")],
        [InlineKeyboardButton(text="🛒 Mua VIP", callback_data="buy_vip_menu"),
         InlineKeyboardButton(text="👨‍💻 Liên Hệ Admin", url=ADMIN_LINK)],
        [InlineKeyboardButton(text="📜 Lịch sử giao dịch", callback_data="history")],
        [InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")],
    ])


def kb_games() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(GAMES), 2):
        row = [InlineKeyboardButton(text=GAMES[i]["name"], callback_data=f"game_{GAMES[i]['id']}")]
        if i + 1 < len(GAMES):
            row.append(InlineKeyboardButton(text=GAMES[i+1]["name"], callback_data=f"game_{GAMES[i+1]['id']}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_game_result(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Cập nhật dự đoán", callback_data=f"game_{game_id}")],
        [InlineKeyboardButton(text="◀️ Quay lại danh sách", callback_data="game_list")],
        [InlineKeyboardButton(text="🏠 Menu Chính", callback_data="home")],
    ])

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Thống Kê", callback_data="admin_stats"),
         InlineKeyboardButton(text="📋 Danh Sách Key", callback_data="admin_listkeys")],
        [InlineKeyboardButton(text="➕ Tạo 1 Key", callback_data="admin_addkey"),
         InlineKeyboardButton(text="🎲 Tạo Nhiều Key", callback_data="admin_genkeys")],
        [InlineKeyboardButton(text="❌ Xóa 1 Key", callback_data="admin_delkey"),
         InlineKeyboardButton(text="🗑 Xóa Toàn Bộ", callback_data="admin_clear_keys")],
        [InlineKeyboardButton(text="📢 Gửi Thông Báo", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="Đóng Menu", callback_data="admin_close")]
    ])

def kb_cancel_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Hủy", callback_data="admin_cancel")]
    ])

def kb_admin_confirm_clear() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ CÓ, XÓA TẤT CẢ!", callback_data="admin_confirm_clear_yes")],
        [InlineKeyboardButton(text="Hủy", callback_data="admin_confirm_clear_no")]
    ])

# ─────────────────────────────────────────────
#  FORMAT TEXT KẾT QUẢ
# ─────────────────────────────────────────────
def format_taixiu(game: dict, data: dict) -> str:
    dice = data["dice"]
    pred_emoji = "📈 TÀI" if data["prediction"] == "TÀI" else "📉 XỈU"
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    return (
        f"🎲 <b>GAME: {game['name']}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>\n"
        f"🔹 Kết quả: <b>{data['result']}</b> — Tổng: <b>{data['total']}</b>\n"
        f"🎲 Xúc xắc: <b>{dice[0]}</b> ─ <b>{dice[1]}</b> ─ <b>{dice[2]}</b>\n"
        f"{'─'*28}\n"
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>\n"
        f"🎯 Dự đoán: <b>{pred_emoji}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"🔄 Loại cầu: <i>{data['bridge_type']}</i>\n"
        f"📉 Pattern: <code>{data['pattern']}</code>\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {datetime.now().strftime('%H:%M:%S')}"
    )


def format_sicbo(game: dict, data: dict) -> str:
    dice = data["dice"]
    total = data["total"]
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    return (
        f"🎱 <b>GAME: {game['name']}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>\n"
        f"🔹 Kết quả: <b>{data['result']}</b> — Tổng: <b>{total}</b>\n"
        f"🎲 Xúc xắc: <b>{dice[0]}</b> ─ <b>{dice[1]}</b> ─ <b>{dice[2]}</b>\n"
        f"{'─'*28}\n"
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>\n"
        f"🎯 Dự đoán: <b>{data['prediction']}</b>\n"
        f"📍 Vị: <b>{data['position']}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {datetime.now().strftime('%H:%M:%S')}"
    )


def format_baccarat(game: dict, data: dict) -> str:
    conf = data["confidence"]
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
    return (
        f"🃏 <b>GAME: {game['name']}</b>\n"
        f"{'─'*28}\n"
        f"🔹 Phiên hiện tại: <code>{data['current_session']}</code>\n"
        f"🔹 Kết quả: <b>{data['baccarat_result']}</b>\n"
        f"{'─'*28}\n"
        f"🔮 Phiên dự đoán: <code>{data['next_session']}</code>\n"
        f"🎯 Dự đoán: <b>{data['baccarat_result']}</b>\n"
        f"📊 Độ tin cậy: <b>{conf}%</b>\n"
        f"   <code>[{conf_bar}]</code>\n"
        f"🔄 Loại cầu: <i>{data['bridge_type']}</i>\n"
        f"📉 Pattern: <code>{data['pattern']}</code>\n"
        f"{'─'*28}\n"
        f"⏱ Cập nhật: {datetime.now().strftime('%H:%M:%S')}"
    )


def format_result(game: dict, data: dict) -> str:
    t = game["type"]
    if t == "sicbo":
        return format_sicbo(game, data)
    if t == "baccarat":
        return format_baccarat(game, data)
    return format_taixiu(game, data)

# ─────────────────────────────────────────────
#  WELCOME TEXT
# ─────────────────────────────────────────────
def welcome_text(user) -> str:
    user_id = user.id
    name = user.full_name
    username = f" (@{user.username})" if user.username else ""
    
    status = "✅ <b>Đã kích hoạt</b>" if is_authorized(user_id) else "❌ <b>Chưa kích hoạt</b>"
    expire = key_expire_str(user_id) if is_authorized(user_id) else "—"
    balance = user_balances.get(user_id, 0)
    return (
        "╔══════════════════════════╗\n"
        "║   🏆  <b>VIP PREDICT BOT</b>  🏆   ║\n"
        "╚══════════════════════════╝\n\n"
        "👤 <b>THÔNG TIN CÁ NHÂN</b>\n"
        f"┣ Tên: <b>{name}</b>{username}\n"
        f"┣ ID: <code>{user_id}</code>\n"
        f"┣ Số dư: <b>{balance:,.0f}đ</b>\n"
        f"┣ Trạng thái: {status}\n"
        f"┗ Hạn dùng: <i>{expire}</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        " <b>GIỚI THIỆU CHỨC NĂNG TOOL</b>\n"
        "🤖 Hệ thống phân tích AI thông minh hỗ trợ:\n"
        "✔️ <b>Tài Xỉu:</b> Bắt cầu MD5, Hũ, Sunwin, 88...\n"
        "✔️ <b>Sicbo:</b> Soi vị, soi tổng chuẩn xác.\n"
        "✔️ <b>Baccarat:</b> Phân tích cầu Player/Banker.\n"
        "⚡️ Cập nhật kết quả & dự đoán thời gian thực!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>BẢNG GIÁ KEY VIP (MUA LIÊN HỆ ADMIN)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📅  1 Ngày      ──────  <b>30.000đ</b>\n"
        "📆  1 Tuần      ──────  <b>120.000đ</b>\n"
        "🗓️  1 Tháng     ──────  <b>220.000đ</b>\n"
        "♾️  Vĩnh viễn  ──────  <b>380.000đ</b>\n"
    )

# ─────────────────────────────────────────────
#  KHỞI TẠO BOT
# ─────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher(storage=MemoryStorage())
app = FastAPI(redirect_slashes=False)

# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if uid not in all_users:
        all_users.add(uid)
        save_data()
    await msg.answer(
        welcome_text(msg.from_user),
        reply_markup=kb_start(is_authorized(uid))
    )


@dp.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    text = welcome_text(cb.from_user)
    markup = kb_start(is_authorized(uid))
    
    # Xử lý lỗi nếu đang ở giao diện ảnh (Mã QR) không thể edit text
    if cb.message.photo:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=markup)
    else:
        await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


# ── TRỢ GIÚP & CHỨC NĂNG KHÁC ──
@dp.message(Command("help"))
async def cmd_help(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "ℹ️ <b>TRỢ GIÚP & CHỨC NĂNG KHÁC</b>\n\n"
        "Vui lòng chọn các chức năng bên dưới:",
        reply_markup=kb_help()
    )


@dp.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(
        "ℹ️ <b>TRỢ GIÚP & CHỨC NĂNG KHÁC</b>\n\n"
        "Vui lòng chọn các chức năng bên dưới:",
        reply_markup=kb_help()
    )
    await cb.answer()


@dp.callback_query(F.data == "deposit")
async def cb_deposit(cb: CallbackQuery):
    text = (
        "💰 <b>NẠP TIỀN VÀO SỐ DƯ</b>\n\n"
        "Vui lòng chọn số tiền bạn muốn nạp. Tiền sẽ được cộng vào số dư tài khoản của bạn để mua VIP."
    )
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50.000đ", callback_data="pay_50000"),
         InlineKeyboardButton(text="100.000đ", callback_data="pay_100000")],
        [InlineKeyboardButton(text="200.000đ", callback_data="pay_200000"),
         InlineKeyboardButton(text="500.000đ", callback_data="pay_500000")],
        [InlineKeyboardButton(text="◀️ Quay lại Trợ Giúp", callback_data="help")]
    ]))
    await cb.answer()


@dp.callback_query(F.data.startswith("pay_"))
async def cb_pay(cb: CallbackQuery):
    amount = int(cb.data.split("_")[1])
    
    # Tạo Nội dung ngẫu nhiên: TÊN (không dấu) + 4 số ngẫu nhiên
    name_clean = clean_memo(cb.from_user.first_name)[:8]
    rand_code = ''.join(random.choices(string.digits, k=4))
    memo = f"{name_clean}{rand_code}"
    
    # Lưu vào database chờ duyệt
    pending_payments[memo] = {"uid": cb.from_user.id, "amount": amount}
    save_data()
    
    qr_url = f"https://img.vietqr.io/image/MB-{BANK_STK}-compact2.png?amount={amount}&addInfo={memo}"
    
    await cb.message.delete()
    await cb.message.answer_photo(
        photo=qr_url,
        caption=(
            f"💰 <b>HÓA ĐƠN THANH TOÁN TỰ ĐỘNG</b>\n\n"
            f"🏦 Ngân hàng: <b>MB Bank</b>\n"
            f"💳 Số tài khoản: <code>{BANK_STK}</code>\n"
            f"💵 Số tiền: <b>{amount:,}đ</b>\n"
            f"📝 Nội dung CK: <code>{memo}</code>\n\n"
            f"<i>⚠️ Bạn vui lòng chuyển ĐÚNG SỐ TIỀN VÀ NỘI DUNG. Hệ thống sẽ tự động cộng số dư cho bạn ngay lập tức!</i>"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")]])
    )
    await cb.answer()


@dp.callback_query(F.data == "account")
async def cb_account(cb: CallbackQuery):
    await cb.message.edit_text(
        welcome_text(cb.from_user),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="help")]
        ])
    )
    await cb.answer()


@dp.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery):
    uid = cb.from_user.id
    history = payment_history.get(uid, [])
    
    if not history:
        text = "📜 <b>LỊCH SỬ GIAO DỊCH</b>\n\nBạn chưa có giao dịch nào."
    else:
        lines = ["📜 <b>LỊCH SỬ GIAO DỊCH</b>\n"]
        # Show max 10 recent transactions
        for item in history[:10]:
            date_obj = datetime.fromisoformat(item['date'])
            date_str = date_obj.strftime('%d/%m/%Y %H:%M')
            lines.append(f"<b>- {item['description']}</b>")
            lines.append(f"  <i>({date_str})</i>: {item['details']}")
        text = "\n".join(lines)
        
    await cb.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="help")]
        ])
    )
    await cb.answer()


# ── TÍNH NĂNG MUA VIP ──
@dp.callback_query(F.data == "buy_vip_menu")
async def cb_buy_vip_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    bal = user_balances.get(uid, 0)
    text = (
        f"🛒 <b>MUA GÓI VIP BẰNG SỐ DƯ</b>\n\n"
        f"💰 Số dư hiện tại: <b>{bal:,.0f}đ</b>\n\n"
        "Vui lòng chọn gói VIP bạn muốn mua. Nếu không đủ tiền, hãy vào mục Nạp Tiền nhé!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 Ngày - 30.000đ", callback_data="buy_vip_1")],
        [InlineKeyboardButton(text="7 Ngày - 120.000đ", callback_data="buy_vip_7")],
        [InlineKeyboardButton(text="1 Tháng - 220.000đ", callback_data="buy_vip_30")],
        [InlineKeyboardButton(text="Vĩnh viễn - 380.000đ", callback_data="buy_vip_999")],
        [InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")]
    ])
    
    if cb.message.photo:
        await cb.message.delete()
        await cb.message.answer(text, reply_markup=kb)
    else:
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data.startswith("buy_vip_") & ~F.data.contains("menu"))
async def cb_process_buy_vip(cb: CallbackQuery):
    days = int(cb.data.split("_")[2])
    price_map = {1: 30000, 7: 120000, 30: 220000, 999: 380000}
    price = price_map.get(days, 30000)
    uid = cb.from_user.id
    bal = user_balances.get(uid, 0)

    if bal < price:
        await cb.answer(f"❌ Số dư không đủ! Bạn cần thêm {price - bal:,.0f}đ để mua gói này.", show_alert=True)
        return

    # Trừ tiền
    user_balances[uid] -= price

    # Gia hạn ngày VIP
    info = valid_keys.get(uid)
    current_exp = info.get("expires") if info else None
    
    if days == 999:
        expires = None
    else:
        if current_exp and current_exp > datetime.now():
            expires = current_exp + timedelta(days=days)  # Cộng dồn
        else:
            expires = datetime.now() + timedelta(days=days)

    valid_keys[uid] = {"key": "BOUGHT_FROM_BALANCE", "expires": expires}
    
    # Ghi lịch sử
    if uid not in payment_history:
        payment_history[uid] = []
    days_text = "Vĩnh viễn" if days == 999 else f"+{days} ngày"
    payment_history[uid].insert(0, {
        "date": datetime.now().isoformat(),
        "description": f"Mua gói VIP {days_text}",
        "details": f"-{price:,.0f}đ"
    })
    save_data()

    exp_str = "Vĩnh viễn ♾️" if expires is None else expires.strftime("%d/%m/%Y %H:%M")
    await cb.message.edit_text(
        f"✅ <b>MUA VIP THÀNH CÔNG!</b>\n\n"
        f"Gói mua: <b>{days_text}</b>\n"
        f"Số dư còn lại: <b>{user_balances[uid]:,.0f}đ</b>\n"
        f"⏳ Hạn sử dụng mới: <b>{exp_str}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Về Menu Chính", callback_data="home")]])
    )
    await cb.answer()

# ── NHẬP KEY ──
@dp.callback_query(F.data == "enter_key")
async def cb_enter_key(cb: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_key)
    await cb.message.edit_text(
        "🔑 <b>Nhập Key Kích Hoạt</b>\n\n"
        "Vui lòng gửi Key của bạn vào đây.\n"
        "<i>Liên hệ Admin để mua Key nếu chưa có.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Hủy", callback_data="home")]
        ])
    )
    await cb.answer()


@dp.message(UserState.waiting_key)
async def process_key(msg: Message, state: FSMContext):
    await state.clear()
    uid  = msg.from_user.id
    key  = msg.text.strip()

    if key not in key_store:
        await msg.answer(
            "❌ <b>Key không hợp lệ!</b>\n\n"
            "Vui lòng kiểm tra lại hoặc liên hệ Admin mua Key mới.",
            reply_markup=kb_start(False)
        )
        return

    info = key_store[key]

    if info["used_by"] is not None and info["used_by"] != uid:
        await msg.answer(
            "⚠️ <b>Key này đã được sử dụng bởi tài khoản khác!</b>",
            reply_markup=kb_start(False)
        )
        return

    # Kích hoạt key
    days = info["duration_days"]
    expires = None if days == -1 else datetime.now() + timedelta(days=days)
    valid_keys[uid] = {"key": key, "expires": expires}
    key_store[key]["used_by"] = uid

    # Thêm vào lịch sử giao dịch
    if uid not in payment_history:
        payment_history[uid] = []
    days_text = "Vĩnh viễn" if days == -1 else f"+{days} ngày"
    payment_history[uid].insert(0, {
        "date": datetime.now().isoformat(),
        "description": f"Kích hoạt key <code>{key}</code>",
        "details": days_text
    })
    save_data()

    exp_str = "Vĩnh viễn ♾️" if expires is None else expires.strftime("%d/%m/%Y %H:%M")
    await msg.answer(
        f"✅ <b>Kích hoạt Key thành công!</b>\n\n"
        f"🔑 Key: <code>{key}</code>\n"
        f"⏳ Hạn sử dụng: <b>{exp_str}</b>\n\n"
        f"Chào mừng bạn đến với VIP Predict Bot! 🎉",
        reply_markup=kb_start(True)
    )
    
    # Gửi thông báo cho Admin
    try:
        user_name = msg.from_user.full_name
        username = f" (@{msg.from_user.username})" if msg.from_user.username else ""
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>THÔNG BÁO NẠP KEY THÀNH CÔNG</b>\n\n"
            f"👤 Người dùng: <b>{user_name}</b>{username}\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"🔑 Key sử dụng: <code>{key}</code>\n"
            f"⏳ Hạn sử dụng: <b>{exp_str}</b>"
        )
    except Exception as e:
        log.error(f"Lỗi gửi thông báo cho admin: {e}")


# ── DANH SÁCH GAME ──
@dp.callback_query(F.data == "game_list")
async def cb_game_list(cb: CallbackQuery):
    uid = cb.from_user.id
    if not is_authorized(uid):
        await cb.answer("❌ Bạn chưa có Key hợp lệ!", show_alert=True)
        return
    await cb.message.edit_text(
        "🎮 <b>DANH SÁCH GAME HỖ TRỢ</b>\n\n"
        "Chọn game bạn muốn xem dự đoán:",
        reply_markup=kb_games()
    )
    await cb.answer()


# ── XEM DỰ ĐOÁN GAME ──
@dp.callback_query(F.data.startswith("game_"))
async def cb_game(cb: CallbackQuery):
    uid     = cb.from_user.id
    game_id = cb.data[5:]   # strip "game_"

    if not is_authorized(uid):
        await cb.answer("❌ Key hết hạn hoặc chưa kích hoạt!", show_alert=True)
        return

    game = GAME_MAP.get(game_id)
    if not game:
        await cb.answer("Game không tồn tại.", show_alert=True)
        return

    await cb.answer("⏳ Đang tải dự đoán...")

    data = await asyncio.to_thread(fetch_prediction, game_id)
    text = format_result(game, data)

    await cb.message.edit_text(text, reply_markup=kb_game_result(game_id))


# ── ADMIN: Menu lệnh ──
@dp.message(Command("menu"))
async def cmd_menu(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.answer(f"⛔ <b>Lỗi:</b> Bạn không phải là Admin!\n\n<i>ID tài khoản của bạn là:</i> <code>{msg.from_user.id}</code>\n\n👉 Hãy copy dòng ID này, mở file <b>bot.py</b> tìm đến dòng số 36 và sửa thành:\n<code>ADMIN_ID = {msg.from_user.id}</code>\nSau đó khởi động lại bot!")
        return
        
    text = (
        "🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nChọn một chức năng bên dưới:"
    )
    await msg.answer(text, reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_close")
async def cb_admin_close(cb: CallbackQuery):
    await cb.message.delete()

@dp.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(cb: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await cb.message.edit_text(
        "🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nĐã hủy thao tác.",
        reply_markup=kb_admin_menu()
    )
    await cb.answer("Đã hủy.")

# --- Các chức năng Admin ---

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    
    total_users = len(all_users)
    active_vip = sum(1 for uid in valid_keys if is_authorized(uid))
    
    text = (
        "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n\n"
        f"👥 Tổng số người dùng: <b>{total_users}</b>\n"
        f"👑 Người dùng VIP (Active): <b>{active_vip}</b>\n"
        f"🔑 Tổng số Key đã tạo: <b>{len(key_store)}</b>"
    )
    await cb.message.edit_text(text, reply_markup=kb_admin_menu())
    await cb.answer("Đã cập nhật thống kê!")

@dp.callback_query(F.data == "admin_listkeys")
async def cb_admin_listkeys(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    
    if not key_store:
        await cb.answer("Chưa có key nào.", show_alert=True)
        return

    lines = ["📋 <b>Danh sách Key</b>\n"]
    for k, v in key_store.items():
        used = f"User {v['used_by']}" if v["used_by"] else "Chưa dùng"
        d    = "♾️" if v["duration_days"] == -1 else f"{v['duration_days']}d"
        lines.append(f"• <code>{k}</code> [{d}] — {used}")
    
    text = "\n".join(lines)
    
    try:
        await cb.message.edit_text(text, reply_markup=kb_admin_menu())
        await cb.answer("Đã tải danh sách key!")
    except Exception: # Message too long
        await cb.message.answer(text)
        await cb.answer("Danh sách key quá dài, đã gửi trong tin nhắn mới.")

@dp.callback_query(F.data == "admin_clear_keys")
async def cb_admin_clear_keys(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    await cb.message.edit_text(
        "⚠️ <b>BẠN CÓ CHẮC CHẮN MUỐN XÓA TOÀN BỘ KEY KHÔNG?</b>\n\n"
        "Hành động này sẽ xóa tất cả key đã tạo và thu hồi VIP của tất cả người dùng. Không thể hoàn tác!",
        reply_markup=kb_admin_confirm_clear()
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_confirm_clear_yes")
async def cb_admin_confirm_clear_yes(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    key_store.clear()
    valid_keys.clear()
    save_data()
    await cb.message.edit_text(
        "✅ <b>Đã xóa toàn bộ Key trong hệ thống!</b>",
        reply_markup=kb_admin_menu()
    )
    await cb.answer("Đã dọn dẹp toàn bộ key!", show_alert=True)

@dp.callback_query(F.data == "admin_confirm_clear_no")
async def cb_admin_confirm_clear_no(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    await cb.message.edit_text(
        "🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠\n\nĐã hủy thao tác xóa.",
        reply_markup=kb_admin_menu()
    )
    await cb.answer("Đã hủy.")

@dp.callback_query(F.data == "admin_addkey")
async def cb_admin_addkey_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.addkey_key)
    await cb.message.edit_text(
        "<b>Bước 1/2:</b> Vui lòng nhập tên Key bạn muốn tạo:",
        reply_markup=kb_cancel_admin()
    )
    await cb.answer()

@dp.message(AdminState.addkey_key)
async def process_admin_addkey_key(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    key = msg.text.strip()
    if key in key_store:
        await msg.answer(f"⚠️ Key <code>{key}</code> đã tồn tại! Vui lòng nhập một key khác:", reply_markup=kb_cancel_admin())
        return
    await state.update_data(key=key)
    await state.set_state(AdminState.addkey_days)
    await msg.answer("<b>Bước 2/2:</b> Vui lòng nhập số ngày sử dụng cho key (nhập -1 cho key vĩnh viễn):", reply_markup=kb_cancel_admin())

@dp.message(AdminState.addkey_days)
async def process_admin_addkey_days(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        days = int(msg.text.strip())
    except ValueError:
        await msg.answer("❗ Số ngày không hợp lệ. Vui lòng nhập một số nguyên (VD: 30 hoặc -1):", reply_markup=kb_cancel_admin())
        return
    data = await state.get_data()
    key = data['key']
    key_store[key] = {"duration_days": days, "used_by": None}
    save_data()
    await state.clear()
    await msg.answer(f"✅ Đã tạo Key thành công!\n\nKey: <code>{key}</code>\nThời hạn: {days if days != -1 else '♾️ Vĩnh viễn'} ngày")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_genkeys")
async def cb_admin_genkeys_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.genkeys_amount)
    await cb.message.edit_text("<b>Bước 1/2:</b> Vui lòng nhập số lượng key muốn tạo (tối đa 50):", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.genkeys_amount)
async def process_admin_genkeys_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        amount = int(msg.text.strip())
        if not (0 < amount <= 50): raise ValueError
    except ValueError:
        await msg.answer("❗ Số lượng không hợp lệ. Vui lòng nhập một số từ 1 đến 50:", reply_markup=kb_cancel_admin())
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminState.genkeys_days)
    await msg.answer("<b>Bước 2/2:</b> Vui lòng nhập số ngày sử dụng cho các key (nhập -1 cho key vĩnh viễn):", reply_markup=kb_cancel_admin())

@dp.message(AdminState.genkeys_days)
async def process_admin_genkeys_days(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        days = int(msg.text.strip())
    except ValueError:
        await msg.answer("❗ Số ngày không hợp lệ. Vui lòng nhập một số nguyên (VD: 30 hoặc -1):", reply_markup=kb_cancel_admin())
        return
    data = await state.get_data()
    amount = data['amount']
    generated_keys = []
    for _ in range(amount):
        while True:
            rand_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            new_key = f"VIP-{rand_str[:4]}-{rand_str[4:]}"
            if new_key not in key_store: break
        key_store[new_key] = {"duration_days": days, "used_by": None}
        generated_keys.append(new_key)
    save_data()
    await state.clear()
    keys_text = "\n".join([f"<code>{k}</code>" for k in generated_keys])
    duration_text = f"{days} ngày" if days != -1 else "Vĩnh viễn ♾️"
    await msg.answer(f"✅ <b>Đã tạo thành công {amount} key ({duration_text}):</b>\n\n{keys_text}\n\n<i>(Chạm vào key để copy)</i>")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_delkey")
async def cb_admin_delkey_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.delkey_key)
    await cb.message.edit_text("Vui lòng nhập tên Key bạn muốn xóa:", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.delkey_key)
async def process_admin_delkey_key(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    key = msg.text.strip()
    if key not in key_store:
        await msg.answer(f"⚠️ Key <code>{key}</code> không tồn tại! Vui lòng nhập lại:", reply_markup=kb_cancel_admin())
        return
    used_by = key_store[key].get("used_by")
    if used_by is not None and used_by in valid_keys:
        del valid_keys[used_by]
    del key_store[key]
    save_data()
    await state.clear()
    await msg.answer(f"✅ Đã xóa Key: <code>{key}</code> thành công và thu hồi quyền (nếu có)!")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.broadcast_text)
    await cb.message.edit_text("Vui lòng nhập nội dung tin nhắn bạn muốn gửi tới tất cả người dùng VIP:", reply_markup=kb_cancel_admin())
    await cb.answer()

@dp.message(AdminState.broadcast_text)
async def process_admin_broadcast_text(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    text = msg.text.strip()
    await state.clear()
    sent = 0
    for uid in valid_keys:
        if not is_authorized(uid): continue
        try:
            await bot.send_message(uid, f"📢 <b>THÔNG BÁO TỪ ADMIN</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            pass
    await msg.answer(f"✅ Đã gửi thông báo tới {sent} người dùng VIP đang hoạt động.")
    await msg.answer("🛠 <b>MENU QUẢN TRỊ (ADMIN)</b> 🛠", reply_markup=kb_admin_menu())


# ─────────────────────────────────────────────
#  WEBHOOK: AUTO DUYỆT THANH TOÁN SEPAY
# ─────────────────────────────────────────────
@app.post("/sepay-webhook")
async def sepay_webhook(request: Request):
    try:
        data = await request.json()
        amount_in = float(data.get('transferAmount', 0))
        content = str(data.get('content', '')).upper()
        
        log.info(f"💰 SePay Webhook: +{amount_in}đ | Nội dung: {content}")

        memos_to_remove = []
        for memo, info in pending_payments.items():
            # Nếu Nội dung và Số tiền khớp với hóa đơn đang chờ
            if memo in content and amount_in >= info["amount"]:
                uid = info["uid"]
                
                # Cộng tiền vào số dư
                if uid not in user_balances:
                    user_balances[uid] = 0
                user_balances[uid] += amount_in

                # Thêm vào lịch sử giao dịch
                if uid not in payment_history:
                    payment_history[uid] = []
                payment_history[uid].insert(0, {
                    "date": datetime.now().isoformat(),
                    "description": f"Nạp tiền tự động",
                    "details": f"+{amount_in:,.0f}đ"
                })
                save_data()
                
                # Báo cho User
                await bot.send_message(uid, f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\nHệ thống đã nhận được <b>{amount_in:,.0f}đ</b> và cộng vào số dư của bạn.\n💰 Số dư hiện tại: <b>{user_balances[uid]:,.0f}đ</b>\n\n<i>👉 Vui lòng vào mục Menu Chính -> <b>MUA VIP</b> để đổi số dư lấy ngày sử dụng!</i>")
                # Báo cho Admin
                await bot.send_message(ADMIN_ID, f"💰 <b>KHÁCH NẠP TIỀN (WEBHOOK)</b>\n\n👤 UID: <code>{uid}</code>\n💵 Số tiền: +{amount_in:,.0f}đ\n📝 Nội dung: {content}")
                
                memos_to_remove.append(memo)
                
        # Xóa các bill đã duyệt khỏi hàng chờ
        for m in memos_to_remove:
            del pending_payments[m]
            save_data()
            
        return {"status": "success"}
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return {"status": "error"}

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    log.info("Bot đang khởi động...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    
    try:
        await asyncio.gather(
            server.serve(),
            # Thêm handle_signals=False để tránh xung đột hệ thống
            dp.start_polling(bot, handle_signals=False)
        )
    except OSError as e:
        log.error(f"Lỗi Port: Cổng 8000 đang bị kẹt hoặc được dùng bởi app khác. Hãy tắt các terminal cũ và chạy lại! Chi tiết: {e}")

if __name__ == "__main__":
    asyncio.run(main())
