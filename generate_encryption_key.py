from cryptography.fernet import Fernet


def generate_key():
    """Generates a new Fernet key and prints it to the console."""
    key = Fernet.generate_key()
    print("Generated Encryption Key:")
    print(key.decode())
    print("\nCopy this key and add it to your .env file as ENCRYPTION_KEY.")


if __name__ == "__main__":
    generate_key()
