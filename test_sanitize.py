import sys
from auto_yt.services.chatgpt_worker import sanitize_generated_script

script = '''### [INTRO]
Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test.

### [BODY]
This is some other text.

Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test. Hello world, this is a test.

### [OUTRO]
Goodbye.
'''
print("Original:")
print(script)
print("Sanitized:")
print(sanitize_generated_script(script))
