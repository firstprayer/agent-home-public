"""Give the frozen runtime its own TLS roots instead of build-machine paths."""
import os
import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
