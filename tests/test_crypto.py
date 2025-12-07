import pytest
from gabs_api_server import crypto
from cryptography.fernet import Fernet
import os
from unittest.mock import patch

def test_encrypt_decrypt_string():
    # Set a test ENCRYPTION_KEY environment variable for this test
    # (assuming config.py reads from os.getenv)
    test_key = Fernet.generate_key().decode()
    with patch.dict(os.environ, {'ENCRYPTION_KEY': test_key}):
        # Reload crypto module to pick up new key - necessary for module-level variable
        # (Alternatively, pass key directly to crypto functions if they supported it)
        import importlib
        importlib.reload(crypto)
        
        original_string = "my_secret_password"
        encrypted_string = crypto.encrypt(original_string)
        decrypted_string = crypto.decrypt(encrypted_string)
        
        assert original_string == decrypted_string
        assert original_string != encrypted_string # Ensure it's actually encrypted

def test_encrypt_type_error():
    test_key = Fernet.generate_key().decode()
    with patch.dict(os.environ, {'ENCRYPTION_KEY': test_key}):
        import importlib
        importlib.reload(crypto)
        
        with pytest.raises(TypeError, match="Data to encrypt must be a string."):
            crypto.encrypt(123) # type: ignore

def test_decrypt_type_error():
    test_key = Fernet.generate_key().decode()
    with patch.dict(os.environ, {'ENCRYPTION_KEY': test_key}):
        import importlib
        importlib.reload(crypto)
        
        with pytest.raises(TypeError, match="Token to decrypt must be a string."):
            crypto.decrypt(123) # type: ignore
