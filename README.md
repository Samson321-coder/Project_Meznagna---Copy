---
title: Chewata Bot
emoji: 🏍️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Dilla Motorcycle Lottery Bot 🏍️

A professional Telegram bot designed to facilitate motorcycle lottery businesses in Dilla, Ethiopia. Fully localized in Amharic with integrated CBE payment verification and automatic draws.

## ✨ Features

- **🌍 Localized Interface**: Complete Amharic language support for all user interactions.
- **💰 Wallet System**: Users can maintain a balance and track their transactions.
- **🏦 CBE Payment Proof**: Seamless integration for Commercial Bank of Ethiopia (CBE) screenshot uploads with manual admin verification.
- **🔄 Automated Draws**: Random winner selection and broadcast system that triggers automatically when a lottery is sold out.
- **🛠️ Admin Dashboard**: Create lotteries, manage users, and verify payments directly via Telegram commands.
- **📊 Database Flexibility**: Built with SQLAlchemy, supporting both SQLite (local) and PostgreSQL (production).

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Installation

1. **Clone the repository**:
   ```bash
   git clone <your-repo-url>
   cd Project_Lottery
   ```

2. **Install dependencies**:
   ```bash
   pip install python-telegram-bot sqlalchemy python-dotenv psycopg2-binary
   ```

3. **Configure Environment Variables**:
   Create a `.env` file based on [.env.template](.env.template):
   ```env
   TELEGRAM_TOKEN=your_bot_token_here
   ADMIN_ID=your_telegram_user_id
   DATABASE_URL=sqlite:///lottery.db
   ```

4. **Run the Bot**:
   ```bash
   python bot.py
   ```

### Deploying (e.g. Hugging Face Spaces Docker)

The included `Dockerfile` matches [Hugging Face Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker): the Space runs the container as **user ID 1000**, so the app directory must be owned by that user (the Dockerfile does this). If the image ran as root with files in `/app`, **SQLite could not create `lottery.db` and the process would exit on startup** — that is the usual reason the bot “works locally but not when deployed.”

1. In the Space **Settings → Repository secrets** (and variables), add at least:
   - `TELEGRAM_TOKEN` — your bot token from BotFather  
   - `ADMIN_ID` — your Telegram user id (optional but recommended)  
   - `DATABASE_URL` — use a hosted Postgres URL for production, or `sqlite:////data/lottery.db` if you attach Space storage so `/data` is available  

   Secrets are injected as **environment variables** at runtime. A `.env` file is **not** in the git repo (it is gitignored), so the deployment will not see your local `.env` unless you duplicate those values as Space secrets.

2. Ensure the Space README metadata uses `sdk: docker` and `app_port` matches `PORT` (default **7860**), consistent with this project.

3. Optional: set `HF_KEEPALIVE_URL` to your Space public URL so the free tier is pinged periodically (see `bot.py`).

## 📖 Usage

### For Users
- **/start**: Access the main menu.
- **🎫 ቲኬት ግዛ**: View active lotteries and open ticket selection.
- **📂 የእኔ ቲኬቶች**: View your confirmed tickets.
- **👤 የኔ መረጃ**: View/update bank details (with `/setbank`).

### For Admins
- **/admin_help**: Show admin command examples.
- **/add_lottery [Name] "[Description]" [TicketCount]**: Create a lottery from text only.
- **Photo + caption `/add_lottery ...`**: Create a lottery with an image (`image_file_id` is saved).
- **Photo + caption `/set_lottery_photo [LotteryID]`**: Set or replace an existing lottery image.
- **/approve_[TX_ID]**: Approve a pending purchase screenshot and confirm the ticket.

### Admin Examples
```text
/add_lottery Promo "Grand Prize" 100
```

Send a photo with this caption to create with image:
```text
/add_lottery Promo "Grand Prize" 100
```

Send a photo with this caption to update image:
```text
/set_lottery_photo 3
```

## Notes
- Inline lottery "buy" callbacks now support both text cards and photo cards.
- No database migration is required for images because `Lottery.image_file_id` already exists.

## 📁 File Structure

- `bot.py`: Main application logic.
- `models.py`: Database schema (SQLAlchemy).
- `database.py`: Session management.
- `strings.py`: Amharic translation dictionary.
- `lottery.db`: Default SQLite database (created on first run).

---
*Created with ❤️ for the Dilla business community.*
