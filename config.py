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

# Chiave per la crittografia dei dati sensibili (es. password salvate)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Email dell'amministratore
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

# Altre configurazioni
WEBSITE_URL = os.getenv("WEBSITE_URL")
MAX_AUTO_BOOK_RETRIES = 3