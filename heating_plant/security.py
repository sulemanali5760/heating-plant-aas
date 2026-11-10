"""OPC UA security for the twin: X.509 application certificates, trust list, username login.

- Channel: SecurityPolicy Basic256Sha256, MessageSecurityMode SignAndEncrypt (OPC 10000-7 profiles;
  terms as in OPC 10101: uav:securityMode / uav:securityPolicy).
- Certificates: self-signed application instance certificates, created on first start in ./pki/
  (git-ignored). The server only accepts client certificates that are copied into pki/trusted/.
- User: one account; the password comes from the environment, is never stored, and is checked
  against a PBKDF2-HMAC-SHA256 hash with a constant-time compare (Python stdlib).
"""
import hashlib
import hmac
import os
import pathlib
import secrets
import socket

from asyncua.crypto import cert_gen
from asyncua.server.user_managers import User, UserManager, UserRole
from cryptography.x509.oid import ExtendedKeyUsageOID

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKI = ROOT / "pki"
SERVER_URI = "urn:sulemanali5760:heating-plant-aas:server"
CLIENT_URI = "urn:sulemanali5760:heating-plant-aas:client"
PASSWORD_ENV = "HEATING_PLANT_PASSWORD"
USERNAME = "operator"


async def ensure_certificate(name: str, app_uri: str, server: bool, pki: pathlib.Path = PKI) -> tuple[pathlib.Path, pathlib.Path]:
    """Create <name>_cert.der / <name>_key.pem once; reuse afterwards."""
    pki.mkdir(parents=True, exist_ok=True)
    (pki / "trusted").mkdir(exist_ok=True)
    cert, key = pki / f"{name}_cert.der", pki / f"{name}_key.pem"
    if not cert.exists():
        use = [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
        await cert_gen.setup_self_signed_certificate(
            key, cert, app_uri, socket.gethostname(), use,
            {"countryName": "DE", "localityName": "Magdeburg", "organizationName": "heating-plant-aas",
             "commonName": f"heating-plant-aas {name}"})
    return cert, key


def trust(cert: pathlib.Path, pki: pathlib.Path = PKI) -> None:
    """Put a peer certificate into the trust list (what an admin does after checking it)."""
    (pki / "trusted").mkdir(parents=True, exist_ok=True)
    (pki / "trusted" / cert.name).write_bytes(cert.read_bytes())


class PasswordUserManager(UserManager):
    """Username/password login. Anonymous is refused because the server only offers UserName tokens."""

    def __init__(self, username: str, password: str):
        self._user = username
        self._salt = secrets.token_bytes(16)
        self._hash = self._derive(password)

    def _derive(self, password: str) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), self._salt, 200_000)

    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username == self._user and password is not None and hmac.compare_digest(self._derive(password), self._hash):
            return User(role=UserRole.User, name=username)
        return None


def password_from_env() -> str:
    pw = os.environ.get(PASSWORD_ENV)
    if not pw:
        raise SystemExit(f"Set {PASSWORD_ENV} to start the secure server (or use --insecure for a local demo).")
    return pw
