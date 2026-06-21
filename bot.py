import asyncio
import os
import re
import sys
import logging
import random
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from flask import Flask, request as flask_request
from sqlalchemy import or_
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest
import shlex
from pathlib import Path
from dotenv import load_dotenv

from database import init_db, get_session
from models import User, Lottery, Ticket, Transaction
from strings import STRINGS

# Ensure .env is loaded from the project directory (database.py already loads it; this keeps bot.py runnable if imports change).
load_dotenv(Path(__file__).resolve().parent / ".env")

# Enable logging to console
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
PORT = int(os.getenv("PORT", 7860))
# After this many minutes without a message or button press, user must send /start again.
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "15"))
PENDING_RESERVATION_MINUTES = int(os.getenv("PENDING_RESERVATION_MINUTES", "360"))

# --- Run mode: set BOT_MODE=webhook to use webhook instead of polling ---
# BOT_MODE   : "polling" (default) | "webhook"
# WEBHOOK_URL: public HTTPS root of your space, e.g. https://username-spacename.hf.space
#              Required when BOT_MODE=webhook.
# WEBHOOK_SECRET: optional random string; Telegram will include it as
#              X-Telegram-Bot-Api-Secret-Token so we can reject forged requests.
BOT_MODE = os.getenv("BOT_MODE", "polling").strip().lower()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
WEBHOOK_PATH = "/webhook"

# PTB runs at most ONE handler per group. session_gate must be alone in group 0 so it does not
# "consume" the update; real handlers (start, callbacks, …) live in group 1.
_HANDLER_GROUP_SESSION_GATE = 0
_HANDLER_GROUP_MAIN = 1

# Flask app for health checks
web_app = Flask(__name__)

@web_app.route('/')
@web_app.route('/health')
def health_check():
    return {"status": "healthy", "service": "lottery_bot"}, 200

def run_flask():
    try:
        web_app.run(host='0.0.0.0', port=PORT)
    except OSError as e:
        logging.error(
            "Flask health server could not bind to port %s (try another PORT): %s",
            PORT,
            e,
        )


# ---------------------------------------------------------------------------
# Globals used by the Flask /webhook endpoint (set at startup in webhook mode)
# ---------------------------------------------------------------------------
_g_application = None
_g_loop: asyncio.AbstractEventLoop | None = None


@web_app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    """Receive Telegram updates via webhook. No-op in polling mode."""
    if _g_application is None or _g_loop is None:
        return {"error": "service not ready"}, 503
    if WEBHOOK_SECRET:
        token = flask_request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if token != WEBHOOK_SECRET:
            logging.warning("Webhook: rejected request — invalid secret token.")
            return {"error": "forbidden"}, 403
    data = flask_request.get_json(force=True, silent=True)
    if not data:
        return {"error": "bad request"}, 400
    update = Update.de_json(data, _g_application.bot)
    asyncio.run_coroutine_threadsafe(_g_application.process_update(update), _g_loop)
    return {"ok": True}, 200


async def reply_session_expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = STRINGS["session_expired"]
    if update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)
    elif update.message:
        await update.message.reply_text(msg)
    elif update.edited_message:
        await update.edited_message.reply_text(msg)


# CommandHandler('start') alone is unreliable: PTB only matches when the bot_command entity
# starts at UTF-16 offset 0. Some clients add invisible chars / formatting so offset != 0,
# and then NO handler runs. We match /start on plain text (Regex uses re.search on message.text).
_START_CMD_RE = re.compile(r"^\s*/start(?:@\S+)?(?:\s|$)", re.IGNORECASE)


def _is_start_command_message(message) -> bool:
    """True for /start (any case, optional @bot, optional payload)."""
    if not message or not message.text:
        return False
    return bool(_START_CMD_RE.search(message.text))


async def session_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Require a fresh /start after SESSION_TIMEOUT_MINUTES of inactivity (bot process keeps running)."""
    user = update.effective_user
    if not user:
        return

    uid = user.id

    if update.message and _is_start_command_message(update.message):
        return

    session = get_session()
    try:
        u = session.query(User).filter(User.id == uid).first()
        now = datetime.now(timezone.utc)
        if not u or u.last_activity_at is None:
            await reply_session_expired(update, context)
            raise ApplicationHandlerStop
        last = u.last_activity_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            await reply_session_expired(update, context)
            raise ApplicationHandlerStop
        u.last_activity_at = now
        session.commit()
    finally:
        session.close()


def run_hf_keepalive():
    """Optional: GET HF_KEEPALIVE_URL periodically so the Space stays warm (free tier sleep).

    URL resolution order:
    1. HF_KEEPALIVE_URL env var (explicit override).
    2. SPACE_ID env var – automatically injected by HF Spaces (e.g. 'username/space-name').
    3. If neither is set, keep-alive is silently disabled.
    """
    explicit_url = os.getenv("HF_KEEPALIVE_URL", "").strip()
    space_id = os.getenv("SPACE_ID", "").strip()  # auto-set by HF runtime
    if explicit_url:
        url = explicit_url
    elif space_id:
        url = f"https://huggingface.co/spaces/{space_id}"
    else:
        logging.info("HF keepalive disabled: set HF_KEEPALIVE_URL or rely on the auto-set SPACE_ID env var.")
        return
    interval = int(os.getenv("HF_KEEPALIVE_INTERVAL_SEC", "300"))

    def loop():
        while True:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "lottery-bot-keepalive"})
                urllib.request.urlopen(req, timeout=30)
            except Exception as e:
                logging.warning("HF keepalive ping failed: %s", e)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="hf_keepalive").start()
    logging.info("HF keepalive enabled: %s every %ss", url, interval)


def auto_release_tickets(session):
    """Delete pending tickets older than the reservation window."""
    expiry_cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_RESERVATION_MINUTES)
    expired = session.query(Ticket).filter(
        Ticket.status == 'pending',
        Ticket.created_at < expiry_cutoff
    ).all()
    for t in expired:
        session.delete(t)
    session.commit()

def generate_number_grid(lottery, session, user_id):
    """Generate a grid of numbers showing availability."""
    auto_release_tickets(session)
    
    # Get all non-confirmed/pending tickets for this lottery
    tickets = session.query(Ticket).filter(Ticket.lottery_id == lottery.id).all()
    ticket_map = {t.ticket_number: t for t in tickets}
    
    keyboard = []
    row = []
    for i in range(1, lottery.total_tickets + 1):
        status = ticket_map.get(i)
        if status:
            if status.status == 'confirmed':
                btn_text = f"❌ {i}"
                callback_data = "noop"
            elif status.status == 'pending':
                btn_text = f"⏳ {i}"  # Locked for everyone until pending reservation expires.
                callback_data = "noop"
        else:
            btn_text = str(i)
            callback_data = f"pick_{lottery.id}_{i}"
            
        row.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    user_id = update.effective_user.id
    username = update.effective_user.username
    full_name = update.effective_user.full_name

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            user = User(id=user_id, username=username, full_name=full_name)
            session.add(user)
        else:
            user.username = username
            user.full_name = full_name
        user.last_activity_at = datetime.now(timezone.utc)
        session.commit()

        keyboard = [
            [KeyboardButton(STRINGS['btn_buy_ticket'])],
            [KeyboardButton(STRINGS['btn_my_tickets']), KeyboardButton(STRINGS['btn_profile'])],
            [KeyboardButton(STRINGS['btn_help'])]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await msg.reply_text(STRINGS['welcome'], reply_markup=reply_markup)
    except Exception as e:
        # Include user/update context so deployment logs can pinpoint account-specific failures.
        logging.exception(
            "start handler failed: user_id=%s username=%r full_name=%r text=%r error=%s",
            user_id,
            username,
            full_name,
            (msg.text if msg else None),
            e,
        )
        try:
            await msg.reply_text("መስተካከል ላይ ስህተት አለ። ትንሽ ቆይተው እንደገና /start ይሞክሩ።")
        except Exception:
            pass
    finally:
        session.close()

async def handle_lotteries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        lotteries = session.query(Lottery).filter(Lottery.is_active == True).all()

        if not lotteries:
            await update.message.reply_text(STRINGS['no_active_lotteries'])
            return

        for lot in lotteries:
            text = STRINGS['lottery_details'].format(
                name=lot.name, desc=lot.description, price=lot.ticket_price,
                sold=lot.sold_tickets, total=lot.total_tickets
            )
            keyboard = [[InlineKeyboardButton(STRINGS['btn_buy_ticket'], callback_data=f"buy_{lot.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if lot.image_file_id:
                await update.message.reply_photo(photo=lot.image_file_id, caption=text, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup)
    finally:
        session.close()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    logging.info(f"Callback received: {data} from user {user_id}")
    await query.answer()

    async def safe_edit_callback_message(text: str, reply_markup=None):
        msg = query.message
        if msg and msg.photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        elif msg and msg.caption and not msg.text:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup)

    session = get_session()
    try:
        if data == 'noop':
            logging.info("Callback ignored: noop")
            return

        if data.startswith('buy_'):
            lot_id = int(data.split('_')[1])
            logging.info(f"User {user_id} requested grid for lottery {lot_id}")
            lot = session.query(Lottery).filter(Lottery.id == lot_id).first()
            if not lot or not lot.is_active:
                await safe_edit_callback_message("ይህ ሎተሪ ተዘግቷል።")
            else:
                markup = generate_number_grid(lot, session, user_id)
                await safe_edit_callback_message(STRINGS['select_number'], reply_markup=markup)
                
        elif data.startswith('pick_'):
            _, lot_id, num = data.split('_')
            lot_id, num = int(lot_id), int(num)
            logging.info(f"User {user_id} picking number {num} for lottery {lot_id}")

            # Ensure expired pending reservations are released before availability checks.
            auto_release_tickets(session)

            # Check if already taken or currently locked as pending.
            existing = session.query(Ticket).filter(
                Ticket.lottery_id == lot_id, 
                Ticket.ticket_number == num,
                or_(Ticket.status == 'confirmed', Ticket.status == 'pending')
            ).first()
            
            if existing:
                logging.info(f"Number {num} already taken or pending for another user.")
                await safe_edit_callback_message("ይቅርታ፣ ይህ ቁጥር ተይዟል። እባክዎን ሌላ ይምረጡ።")
            else:
                # Keep one active pending number per user per lottery.
                old_pending = session.query(Ticket).filter(
                    Ticket.user_id == user_id,
                    Ticket.lottery_id == lot_id,
                    Ticket.status == 'pending',
                ).all()
                for op in old_pending:
                    session.delete(op)

                mine = Ticket(user_id=user_id, lottery_id=lot_id, ticket_number=num, status='pending')
                session.add(mine)
                logging.info(f"Number {num} reserved (new) for user {user_id}")

                mine.created_at = datetime.now(timezone.utc)
                session.commit()
                await safe_edit_callback_message(STRINGS['number_reserved'].format(number=num))
    except Exception as e:
        logging.error(f"Error in handle_callback: {e}", exc_info=True)
        await query.message.reply_text(f"Error: {e}")
    finally:
        session.close()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or ""
    if caption.strip().startswith('/add_lottery'):
        return await add_lottery(update, context)
    if caption.strip().startswith('/set_lottery_photo'):
        return await set_lottery_photo(update, context)
        
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    
    session = get_session()
    try:
        pending_ticket = session.query(Ticket).filter(
            Ticket.user_id == user_id,
            Ticket.status == 'pending'
        ).order_by(Ticket.created_at.desc()).first()

        if not pending_ticket:
            await update.message.reply_text("እባክዎን መጀመሪያ ቲኬት ቁጥር ይምረጡ።")
            return

        tx = Transaction(
            user_id=user_id,
            ticket_id=pending_ticket.id,
            amount=pending_ticket.lottery.ticket_price,
            type='purchase',
            status='pending',
            screenshot_file_id=photo.file_id
        )
        session.add(tx)
        session.commit()

        await update.message.reply_text(STRINGS['screenshot_received'])

        if ADMIN_ID:
            try:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo.file_id,
                    caption=(f"አዲስ የክፍያ ጥያቄ!\n"
                             f"ተጠቃሚ: {update.effective_user.full_name}\n"
                            #  f"ሎተሪ: {pending_ticket.lottery.name}\n"
                             f"መዝናኛ: {pending_ticket.lottery.name}\n"
                             f"ቁጥር: {pending_ticket.ticket_number}\n"
                             f"TX ID: {tx.id}\n\n"
                             f"ለማረጋገጥ: /approve_{tx.id}")
                )
            except Exception as e:
                logging.error(f"Admin notify error: {e}")
    finally:
        session.close()

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID: return

    try:
        tx_id = int(update.message.text.split('_')[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Error: invalid /approve_ command")
        return

    session = get_session()
    try:
        tx = session.query(Transaction).filter(Transaction.id == tx_id).first()

        if tx and tx.status == 'pending':
            tx.status = 'approved'
            ticket = tx.ticket
            if ticket:
                ticket.status = 'confirmed'
                ticket.lottery.sold_tickets += 1

                await update.message.reply_text(f"ቲኬት {ticket.ticket_number} ተረጋግጧል።")
                await context.bot.send_message(
                    chat_id=tx.user_id,
                    text=STRINGS['ticket_confirmed'].format(ticket_num=ticket.ticket_number, lottery_name=ticket.lottery.name)
                )

                if ticket.lottery.sold_tickets >= ticket.lottery.total_tickets:
                    await perform_draw(ticket.lottery, context, session)

            session.commit()
        elif tx:
            await update.message.reply_text(f"TX {tx_id} ሁኔታ: {tx.status} — ማረጋገጥ አልተቻለም።")
        else:
            await update.message.reply_text(f"TX {tx_id} አልተገኘም።")
    except Exception as e:
        logging.error(f"Error in admin_approve: {e}", exc_info=True)
        await update.message.reply_text(f"ስህተት: {e}")
    finally:
        session.close()


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a pending payment: mark transaction rejected, free the ticket number, notify user."""
    if str(update.effective_user.id) != ADMIN_ID:
        return

    try:
        tx_id = int(update.message.text.split('_')[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Error: invalid /reject_ command")
        return

    session = get_session()
    try:
        tx = session.query(Transaction).filter(Transaction.id == tx_id).first()

        if not tx:
            await update.message.reply_text(f"TX {tx_id} አልተገኘም።")
            return

        if tx.status != 'pending':
            await update.message.reply_text(f"TX {tx_id} ሁኔታ: {tx.status} — ውድቅ ማድረግ አልተቻለም።")
            return

        tx.status = 'rejected'
        ticket = tx.ticket
        ticket_num = None
        if ticket:
            ticket_num = ticket.ticket_number
            # Release the number so other users can pick it.
            session.delete(ticket)

        session.commit()

        await update.message.reply_text(
            f"❌ TX {tx_id} ውድቅ ተደርጓል።"
            + (f" ቁጥር {ticket_num} ተለቋል።" if ticket_num else "")
        )
        try:
            await context.bot.send_message(
                chat_id=tx.user_id,
                text=STRINGS['ticket_rejected'].format(ticket_num=ticket_num or '?')
            )
        except Exception as notify_err:
            logging.error(f"Failed to notify user {tx.user_id} of rejection: {notify_err}")
    except Exception as e:
        logging.error(f"Error in admin_reject: {e}", exc_info=True)
        await update.message.reply_text(f"ስህተት: {e}")
    finally:
        session.close()

async def perform_draw(lottery, context, session):
    tickets = session.query(Ticket).filter(Ticket.lottery_id == lottery.id, Ticket.status == 'confirmed').all()
    if not tickets: return
    
    num_winners = min(3, len(tickets))
    winning_tickets = random.sample(tickets, num_winners)
    
    lottery.is_active = False
    
    winners_text = ""
    medals = ["🥇 1ኛ", "🥈 2ኛ", "🥉 3ኛ"]
    
    for i, winner_ticket in enumerate(winning_tickets):
        winner_user = winner_ticket.user
        winner_name = winner_user.full_name or winner_user.username or "Unknown"
        winners_text += f"{medals[i]} አሸናፊ፡ {winner_name} (ቲኬት፥ {winner_ticket.ticket_number})\n"
        
        if ADMIN_ID:
            try:
                admin_msg = f"🏆 አሸናፊ: {winner_name} (ቲኬት {winner_ticket.ticket_number})\nባንክ ዝርዝር:\nባንክ፡ {winner_user.bank_name or 'አልገባም'}\nሂሳብ፡ {winner_user.bank_account_number or 'አልገባም'}"
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg)
            except Exception as e:
                logging.error(f"Failed to send admin bank info: {e}")
    
    announcement = STRINGS['winner_announcement_multiple'].format(
        lottery_name=lottery.name,
        winners_list=winners_text
    )

    if TELEGRAM_CHANNEL_ID and str(TELEGRAM_CHANNEL_ID).strip():
        try:
            await context.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID.strip(), text=announcement)
            logging.info(f"Announced winner draw results for lottery '{lottery.name}' to channel {TELEGRAM_CHANNEL_ID}")
        except Exception as ch_err:
            logging.error(f"Failed to send winner announcement to channel: {ch_err}")
    
    unique_users = session.query(User).join(Ticket).filter(Ticket.lottery_id == lottery.id).distinct().all()
    for user in unique_users:
        try:
            await context.bot.send_message(chat_id=user.id, text=announcement)
        except: pass

async def setbank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await update.message.reply_text(STRINGS['bank_set_format'])
            return

        bank_name = parts[1]
        account_number = parts[2]

        session = get_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                user.bank_name = bank_name
                user.bank_account_number = account_number
                session.commit()
                await update.message.reply_text(STRINGS['bank_set_success'])
        finally:
            session.close()
    except Exception:
        await update.message.reply_text(STRINGS['bank_set_format'])

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != (ADMIN_ID or "").strip():
        await update.message.reply_text(STRINGS['admin_only_command'])
        return
    await update.message.reply_text(STRINGS['admin_help_text'])

async def add_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logging.info(f"Admin command '/add_lottery' from user {user_id}")
    if str(user_id) != (ADMIN_ID or "").strip():
        logging.warning(f"Unauthorized admin attempt from {user_id}. Expected {ADMIN_ID}")
        await update.message.reply_text(STRINGS['admin_only_command'])
        return
    try:
        msg_text = update.message.text or update.message.caption
        if not msg_text:
            raise ValueError("Missing arguments")
        
        # Parse text cleanly ignoring the command itself
        cmd = msg_text.split()[0]
        cmd_text = msg_text.replace(cmd, "", 1).strip()
        parts = shlex.split(cmd_text)
        
        # Expected: name "desc" price tickets
        if len(parts) < 4:
            raise ValueError("Missing arguments")
            
        name = parts[0]
        tickets = int(parts[-1])
        price = float(parts[-2])
        desc = " ".join(parts[1:-2]).strip('"\'”“')
        
        img_id = update.message.photo[-1].file_id if update.message.photo else None
        
        session = get_session()
        try:
            new_lot = Lottery(name=name, description=desc, total_tickets=tickets, ticket_price=price, image_file_id=img_id)
            session.add(new_lot)
            session.commit()
            if img_id:
                await update.message.reply_text(STRINGS['lottery_created_with_photo'].format(name=name, lottery_id=new_lot.id))
            else:
                await update.message.reply_text(STRINGS['lottery_created_no_photo'].format(name=name, lottery_id=new_lot.id))

            if TELEGRAM_CHANNEL_ID and str(TELEGRAM_CHANNEL_ID).strip():
                try:
                    text = STRINGS['lottery_details'].format(
                        name=new_lot.name, desc=new_lot.description, price=new_lot.ticket_price,
                        sold=0, total=new_lot.total_tickets
                    )
                    announcement_text = f"🆕 አዲስ መዝናኛ ተጀምሯል! \n\n@cheweta_meznagna_bot \nወይም \nt.me/akeray_tekeray_bot \nTelegram Bot ላይ ይዝናኑ | ያሸንፉ | ይሸለሙ\n\n{text}"
                    if img_id:
                        await context.bot.send_photo(chat_id=TELEGRAM_CHANNEL_ID.strip(), photo=img_id, caption=announcement_text)
                    else:
                        await context.bot.send_message(chat_id=TELEGRAM_CHANNEL_ID.strip(), text=announcement_text)
                    logging.info(f"Announced new lottery '{name}' to channel {TELEGRAM_CHANNEL_ID}")
                except Exception as ch_err:
                    logging.error(f"Failed to send channel announcement for new lottery: {ch_err}")
        finally:
            session.close()
    except Exception as e:
        logging.error(f"Error in add_lottery: {e}")
        await update.message.reply_text(STRINGS['add_lottery_usage'])


async def set_lottery_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logging.info(f"Admin command '/set_lottery_photo' from user {user_id}")
    if str(user_id) != (ADMIN_ID or "").strip():
        logging.warning(f"Unauthorized admin attempt from {user_id}. Expected {ADMIN_ID}")
        await update.message.reply_text(STRINGS['admin_only_command'])
        return

    message = update.message
    msg_text = message.text or message.caption or ""
    cmd_text = msg_text.replace(msg_text.split()[0], "", 1).strip() if msg_text.strip() else ""
    parts = cmd_text.split()

    if len(parts) != 1:
        await message.reply_text(STRINGS['set_lottery_photo_usage'])
        return

    try:
        lottery_id = int(parts[0])
    except ValueError:
        await message.reply_text(STRINGS['set_lottery_photo_invalid_id'])
        return

    if not message.photo:
        await message.reply_text(STRINGS['set_lottery_photo_missing_photo'])
        return

    img_id = message.photo[-1].file_id
    session = get_session()
    try:
        lot = session.query(Lottery).filter(Lottery.id == lottery_id).first()
        if not lot:
            await message.reply_text(STRINGS['set_lottery_photo_not_found'].format(lottery_id=lottery_id))
            return

        lot.image_file_id = img_id
        session.commit()
        await message.reply_text(
            STRINGS['set_lottery_photo_success'].format(lottery_id=lot.id, lottery_name=lot.name)
        )
    finally:
        session.close()

if __name__ == '__main__':
    if not TOKEN or not str(TOKEN).strip():
        logging.error(
            "TELEGRAM_TOKEN is missing. Set it in the project .env file or in the environment "
            "(e.g. TELEGRAM_TOKEN=...). If you run from another folder, the .env next to bot.py is still loaded."
        )
        sys.exit(1)

    init_db()

    # Create a request object with customized timeouts to handle network lag and large files.
    ptb_request = HTTPXRequest(connect_timeout=60, read_timeout=60, write_timeout=60, pool_timeout=60)
    application = ApplicationBuilder().token(TOKEN).request(ptb_request).build()
    application.add_handler(TypeHandler(Update, session_gate), group=_HANDLER_GROUP_SESSION_GATE)
    # See _START_CMD_RE: CommandHandler('start') is unreliable; MessageHandler must be group 1, not 0.
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(_START_CMD_RE), start),
        group=_HANDLER_GROUP_MAIN,
    )
    application.add_handler(CommandHandler('admin_help', admin_help), group=_HANDLER_GROUP_MAIN)
    application.add_handler(CommandHandler('setbank', setbank), group=_HANDLER_GROUP_MAIN)
    application.add_handler(CommandHandler('add_lottery', add_lottery), group=_HANDLER_GROUP_MAIN)
    application.add_handler(CommandHandler('set_lottery_photo', set_lottery_photo), group=_HANDLER_GROUP_MAIN)
    application.add_handler(MessageHandler(filters.Regex('^/approve_'), admin_approve), group=_HANDLER_GROUP_MAIN)
    application.add_handler(MessageHandler(filters.Regex('^/reject_'), admin_reject), group=_HANDLER_GROUP_MAIN)

    async def msg_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user_id = update.effective_user.id
        logging.info(f"ROUTER: Message from {user_id}: '{text}'")

        # Robust string matching
        buy_text = STRINGS['btn_buy_ticket']
        ticket_text = STRINGS['btn_my_tickets']
        help_text = STRINGS['btn_help']
        profile_text = STRINGS['btn_profile']

        if text == buy_text or buy_text in text:
            logging.info("Routing to handle_lotteries")
            await handle_lotteries(update, context)
        elif text == ticket_text or ticket_text in text:
            logging.info("Routing to my_tickets")
            session = get_session()
            try:
                tickets = session.query(Ticket).filter(Ticket.user_id == user_id, Ticket.status == 'confirmed').all()
                if not tickets:
                    await update.message.reply_text("ምንም የገዙት ቲኬት የለም።")
                else:
                    resp = "የእርስዎ ቲኬቶች፡\n"
                    for t in tickets:
                        resp += f"- {t.lottery.name}: #{t.ticket_number}\n"
                    await update.message.reply_text(resp)
            finally:
                session.close()
        elif text == profile_text or profile_text in text:
            logging.info("Routing to profile")
            session = get_session()
            try:
                user = session.query(User).filter(User.id == user_id).first()
                if user:
                    if not user.bank_name:
                        await update.message.reply_text(STRINGS['profile_text_no_bank'])
                    else:
                        await update.message.reply_text(
                            STRINGS['profile_text_has_bank'].format(
                                bank_name=user.bank_name, account_number=user.bank_account_number
                            )
                        )
            finally:
                session.close()
        elif text == help_text or help_text in text:
            await update.message.reply_text("ለመጀመር፡ /start ብለዉ ከጻፉ በኃላ Enterን ይጫኑ \n\nቲከቶች ተሽጠዉ እንዳለቁ Systemዉ authomatically አሸናፊዎችን ለይቶ ለሁሉም ተሳታፊዎች ይገልጻል \n\nያሸነፉ ቁጥሮችን t.me/meznagna_26 Telegram Channel ላይ ይመልከቱ")
        else:
            logging.info(f"Unknown input: '{text}'")

    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), msg_router),
        group=_HANDLER_GROUP_MAIN,
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo), group=_HANDLER_GROUP_MAIN)
    application.add_handler(CallbackQueryHandler(handle_callback), group=_HANDLER_GROUP_MAIN)

    async def raw_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logging.info(f"RAW UPDATE RECEIVED: {update.to_json()}")
    application.add_handler(TypeHandler(Update, raw_logger), group=-1)

    # ------------------------------------------------------------------
    # Mode selection: polling (default) vs webhook
    # ------------------------------------------------------------------
    if BOT_MODE == "webhook":
        if not WEBHOOK_URL:
            logging.error(
                "BOT_MODE=webhook requires WEBHOOK_URL to be set "
                "(e.g. WEBHOOK_URL=https://username-spacename.hf.space)."
            )
            sys.exit(1)

        # Expose the application and its event loop to the Flask /webhook route.
        _g_loop = asyncio.new_event_loop()
        _g_application = application

        async def _run_webhook():
            """Initialize PTB, register the webhook with Telegram, then block."""
            full_url = WEBHOOK_URL.rstrip('/') + WEBHOOK_PATH
            await application.initialize()
            await application.bot.set_webhook(
                url=full_url,
                secret_token=WEBHOOK_SECRET or None,
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
            logging.info("Webhook registered: %s", full_url)
            await application.start()
            try:
                # Block indefinitely; Flask (main thread) is the actual server.
                await asyncio.Event().wait()
            finally:
                logging.info("Webhook mode shutting down...")
                await application.bot.delete_webhook()
                await application.stop()
                await application.shutdown()

        def _start_ptb_webhook():
            asyncio.set_event_loop(_g_loop)
            _g_loop.run_until_complete(_run_webhook())

        threading.Thread(target=_start_ptb_webhook, daemon=True, name="ptb_webhook").start()
        run_hf_keepalive()
        logging.info("Bot is starting in WEBHOOK mode on port %s ...", PORT)
        # Flask is the main blocking server; it receives Telegram POSTs at /webhook.
        web_app.run(host='0.0.0.0', port=PORT)

    else:
        # Polling mode: Flask health server runs in a background thread.
        threading.Thread(target=run_flask, daemon=True).start()
        run_hf_keepalive()
        logging.info("Bot is starting in POLLING mode...")
        # Increase polling timeout and bootstrap retries.
        # drop_pending_updates=True clears old messages to avoid a backlog on restart.
        application.run_polling(timeout=30, bootstrap_retries=5, drop_pending_updates=True)
