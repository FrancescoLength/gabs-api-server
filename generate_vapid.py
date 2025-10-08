from pywebpush import VAPIDKeys

keys = VAPIDKeys.generate()
print(f'VAPID_PUBLIC_KEY = "{keys["public_key"]}"')
print(f'VAPID_PRIVATE_KEY = "{keys["private_key"]}"')