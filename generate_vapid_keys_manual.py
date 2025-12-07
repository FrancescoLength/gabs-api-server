from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from cryptography.hazmat.backends import default_backend
import base64

def urlsafe_base64_encode(data):
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

# Generate a new private key
private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

# Get the public key
public_key = private_key.public_key()

# Serialize the private key
private_pem = private_key.private_bytes(
    Encoding.DER,
    PrivateFormat.PKCS8,
    NoEncryption()
)

# Serialize the public key
public_pem = public_key.public_bytes(
    Encoding.X962,
    PublicFormat.UncompressedPoint
)

# Encode to URL-safe base64
vapid_private_key = urlsafe_base64_encode(private_pem)
vapid_public_key = urlsafe_base64_encode(public_pem)

print(f'VAPID_PUBLIC_KEY = "{vapid_public_key}"')
print(f'VAPID_PRIVATE_KEY = "{vapid_private_key}"')
