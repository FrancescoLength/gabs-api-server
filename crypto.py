from cryptography.fernet import Fernet
from . import config

# Initialize Fernet with the key from config
# This will raise an error if the key is not set, which is a good thing.
cipher_suite = Fernet(config.ENCRYPTION_KEY.encode())

def encrypt(data: str) -> str:
    """Encrypts a string and returns it as a URL-safe string."""
    if not isinstance(data, str):
        raise TypeError("Data to encrypt must be a string.")
    
    encrypted_data = cipher_suite.encrypt(data.encode())
    return encrypted_data.decode()

def decrypt(token: str) -> str:
    """Decrypts a token and returns the original string."""
    if not isinstance(token, str):
        raise TypeError("Token to decrypt must be a string.")
        
    decrypted_data = cipher_suite.decrypt(token.encode())
    return decrypted_data.decode()
