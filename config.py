import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
THREAD_TTL_HOURS = int(os.getenv("THREAD_TTL_HOURS", "4"))
PORT = int(os.getenv("PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./bhs_sms.db")
