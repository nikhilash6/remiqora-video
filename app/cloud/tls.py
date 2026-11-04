"""TLS for a rented server without an https proxy (Vast.ai: plain http://IP:port). The STUDIO makes the key and a
self-signed certificate here, hands both to the server in its environment when the instance is created
(TLS=1, TLS_CERT / TLS_KEY as base64 PEM - cloud/worker_server.py serves https / wss with them) and keeps the
certificate in the pod's record. Every connection then trusts exactly that certificate (app/engine/remote.py
pinned_context): no CA, no host name, no «trust on first use» - the server never has to tell the studio its own
fingerprint over an unprotected line.

The certificate is made with the `cryptography` package when the studio has it, else with the `openssl` command
(Git for Windows brings one); the fingerprint (sha256 of the DER) needs neither.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path

from .. import i18n
from .providers.base import ProviderError

CN = "remiqora-video-worker"
DAYS = 3650


def fingerprint(cert_pem: str) -> str:
    """sha256 of the certificate's DER, hex - what the page shows and what the record keeps beside the PEM."""
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert_pem)).hexdigest()


def make() -> tuple[str, str]:
    """-> (certificate PEM, private key PEM): EC P-256, self-signed, CA:TRUE so OpenSSL takes it as its own trust
    anchor even with strict checks on."""
    try:
        return _with_cryptography()
    except ImportError:
        pass
    if shutil.which("openssl"):
        return _with_openssl()
    raise ProviderError("other", i18n.L("Нужен пакет cryptography в окружении студии (pip install cryptography) или openssl в PATH: "
                                        "из них делается сертификат для шифрования связи с сервером",
                                        "The studio needs the cryptography package (pip install cryptography) or openssl in PATH: "
                                        "they make the certificate that encrypts the link to the server"))


def _with_cryptography() -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CN)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, crl_sign=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM).decode(), key_pem.decode()


def _with_openssl() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="rv-tls-") as d:
        cert, key = Path(d) / "cert.pem", Path(d) / "key.pem"
        cmd = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
               "-keyout", str(key), "-out", str(cert), "-days", str(DAYS), "-subj", f"/CN={CN}", "-sha256",
               "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,digitalSignature,keyCertSign,keyEncipherment"]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise ProviderError("other", f"openssl: {r.stderr.strip()[:300]}")
        return cert.read_text(encoding="utf-8"), key.read_text(encoding="utf-8")


def env(cert_pem: str, key_pem: str) -> dict:
    """The server's environment for these (cloud/worker_server.py: TLS)."""
    return {"TLS": "1", "TLS_CERT": base64.b64encode(cert_pem.encode()).decode(), "TLS_KEY": base64.b64encode(key_pem.encode()).decode()}
