import os
from dotenv import load_dotenv

# Carica le variabili d'ambiente da un file .env
load_dotenv()

# Chiave segreta per firmare i JWT. Caricata dalla variabile d'ambiente.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

# Chiavi VAPID per le notifiche push. Caricate dalle variabili d'ambiente.
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_ADMIN_EMAIL = os.getenv("VAPID_ADMIN_EMAIL")

ENCRYPTION_KEY: str | None = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
    # Fallback for legacy file support, but prioritize ENV.
    # This path should be deprecated in future versions.
    ENCRYPTION_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'encryption.key')
    try:
        with open(ENCRYPTION_KEY_FILE, 'r') as f:
            ENCRYPTION_KEY = f.read().strip()
    except FileNotFoundError:
        pass

if not ENCRYPTION_KEY:
    raise RuntimeError("CRITICAL: ENCRYPTION_KEY not found in environment variables.")

# Email dell'amministratore
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

# Altre configurazioni
WEBSITE_URL = os.getenv("WEBSITE_URL")
MAX_AUTO_BOOK_RETRIES = 3