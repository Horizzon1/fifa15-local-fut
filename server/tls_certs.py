"""TLS certificates that EA's ProtoSSL stack will accept.

FIFA-era clients embed a very old TLS implementation. Three certificate flavours
are supported so the right one can be chosen empirically for FIFA 15:

  * `old-protossl` - a deliberately malformed chain. Public Bug_OldProtoSSL
    research shows that setting only the OUTER signatureAlgorithm OID to
    rsaEncryption (while the inner TBSCertificate keeps md5WithRSAEncryption)
    makes affected ProtoSSL builds compare a zero-length digest and accept it.
  * `sha1`   - a legacy SHA-1 RSA chain.
  * `sha256` - a normal modern chain, for tooling and health checks.

Only ever served on 127.0.0.1 for this local research server.

Approach derived from the FIFA 14 Local FUT project
(https://github.com/KyroGeorge2/FIFA-14-Local-FUT).
"""
from __future__ import annotations

import os
import shutil
import ssl
import subprocess
from pathlib import Path

# OpenSSL 3 refuses MD5/SHA-1 signing and short RSA keys unless the legacy
# provider and SECLEVEL=0 are enabled, so every invocation uses this config.
LEGACY_OPENSSL_CONF = """openssl_conf = openssl_init
[openssl_init]
providers = provider_sect
alg_section = algorithm_sect
ssl_conf = ssl_sect
[provider_sect]
default = default_sect
legacy = legacy_sect
[default_sect]
activate = 1
[legacy_sect]
activate = 1
[algorithm_sect]
[ssl_sect]
system_default = system_default_sect
[system_default_sect]
CipherString = DEFAULT:@SECLEVEL=0
MinProtocol = TLSv1
[req]
distinguished_name = req_distinguished_name
prompt = no
[req_distinguished_name]
"""

# EA's real chain identifies itself this way; matching it keeps any client-side
# issuer expectations satisfied.
CA_SUBJECT = (
    "/OU=Online Technology Group/O=Electronic Arts, Inc./L=Redwood City"
    "/ST=California/C=US/CN=OTG3 Certificate Authority"
)

MD5_RSA_OID = bytes.fromhex("2a864886f70d010104")


def find_openssl() -> Path:
    """Locate an openssl binary, preferring one already installed on the machine."""
    found = shutil.which("openssl")
    if found:
        return Path(found)
    for candidate in (
        Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
        Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
        Path(r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe"),
    ):
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "openssl was not found. Install Git for Windows (which bundles it) or OpenSSL."
    )


def _run(openssl: Path, args: list[str], directory: Path) -> None:
    env = dict(os.environ)
    # openssl runs with cwd set to `directory`, so the config must be absolute.
    env["OPENSSL_CONF"] = str((directory / "openssl-legacy.cnf").resolve())
    result = subprocess.run(
        [str(openssl), *args],
        cwd=str(directory),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"openssl {' '.join(args[:2])} failed ({result.returncode}): {result.stderr.strip()}"
        )


def _subject_for(hostname: str) -> str:
    return f"/CN={hostname}/OU=Global Online Studio/O=Electronic Arts, Inc./ST=California/C=US"


def _write_config(directory: Path, hostname: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "openssl-legacy.cnf").write_text(LEGACY_OPENSSL_CONF, encoding="ascii")
    extensions = directory / f"{_safe(hostname)}.ext.cnf"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        f"subjectAltName=DNS:{hostname},DNS:*.ea.com,DNS:*.easports.com,DNS:localhost,IP:127.0.0.1\n",
        encoding="ascii",
    )
    return extensions


def _safe(hostname: str) -> str:
    return hostname.replace("*", "star").replace(".", "_")


def _usable(cert_path: Path, key_path: Path) -> bool:
    if not (cert_path.exists() and key_path.exists()):
        return False
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.set_ciphers("ALL:@SECLEVEL=0")
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        return True
    except (OSError, ssl.SSLError):
        return False


def create_certificate(
    hostname: str, directory: Path, mode: str = "old-protossl", force: bool = False
) -> tuple[Path, Path, Path]:
    """Create (or reuse) a server certificate. Returns (cert, key, ca)."""
    # openssl is invoked with cwd=directory, so every path handed to it must be
    # absolute or it resolves relative to the directory twice.
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    safe = _safe(hostname)
    cert_path = directory / f"{safe}.{mode}.crt"
    key_path = directory / f"{safe}.{mode}.key"
    ca_path = directory / f"{mode}-ca.pem"
    ca_key_path = directory / f"{mode}-ca.key"
    csr_path = directory / f"{safe}.{mode}.csr"

    if not force and _usable(cert_path, key_path) and ca_path.exists():
        return cert_path, key_path, ca_path

    extensions = _write_config(directory, hostname)
    openssl = find_openssl()

    digest = {"old-protossl": "-md5", "sha1": "-sha1", "sha256": "-sha256"}.get(mode)
    if digest is None:
        raise ValueError(f"unknown certificate mode {mode!r}")
    key_bits = "1024" if mode in ("old-protossl", "sha1") else "2048"

    _run(openssl, [
        "req", "-x509", f"-newkey", f"rsa:{key_bits}", digest, "-nodes",
        "-days", "3650", "-subj", CA_SUBJECT,
        "-keyout", str(ca_key_path), "-out", str(ca_path),
    ], directory)

    _run(openssl, [
        "req", "-new", "-newkey", f"rsa:{key_bits}", "-nodes",
        "-subj", _subject_for(hostname),
        "-keyout", str(key_path), "-out", str(csr_path),
    ], directory)

    _run(openssl, [
        "x509", "-req", "-in", str(csr_path), "-CA", str(ca_path),
        "-CAkey", str(ca_key_path), "-set_serial", "1", "-days", "3650",
        digest, "-extfile", str(extensions), "-out", str(cert_path),
    ], directory)

    if mode == "old-protossl":
        _apply_old_protossl_quirk(openssl, cert_path, directory, safe)

    return cert_path, key_path, ca_path


def _apply_old_protossl_quirk(openssl: Path, cert_path: Path, directory: Path, safe: str) -> None:
    """Rewrite only the outer signatureAlgorithm OID from md5WithRSAEncryption
    (1.2.840.113549.1.1.4) to rsaEncryption (…1.1.1)."""
    der_path = directory / f"{safe}.der"
    patched_path = directory / f"{safe}.patched.der"

    _run(openssl, ["x509", "-outform", "der", "-in", str(cert_path), "-out", str(der_path)], directory)

    encoded = bytearray(der_path.read_bytes())
    occurrences = []
    start = 0
    while (index := encoded.find(MD5_RSA_OID, start)) >= 0:
        occurrences.append(index)
        start = index + 1
    if len(occurrences) < 2:
        raise RuntimeError(
            f"expected two md5WithRSAEncryption OIDs in the certificate, found {len(occurrences)}"
        )

    # occurrences[0] is inside TBSCertificate and must stay intact; the second is
    # the outer signatureAlgorithm.
    encoded[occurrences[1] + len(MD5_RSA_OID) - 1] = 0x01
    patched_path.write_bytes(encoded)

    _run(openssl, ["x509", "-inform", "der", "-in", str(patched_path), "-out", str(cert_path)], directory)


def create_tls_context(
    hostname: str, directory: Path, mode: str = "old-protossl"
) -> tuple[ssl.SSLContext, Path]:
    """Build a maximally permissive server context using the chosen certificate."""
    cert_path, key_path, ca_path = create_certificate(hostname, directory, mode)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # EA-era clients negotiate ancient suites; the security level has to drop for
    # the handshake to complete at all.
    context.minimum_version = ssl.TLSVersion.TLSv1
    try:
        context.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        context.set_ciphers("ALL")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context, ca_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--hostname", default="spring14.gosredirector.ea.com")
    parser.add_argument("--directory", type=Path, default=Path("certs"))
    parser.add_argument("--mode", default="old-protossl", choices=["old-protossl", "sha1", "sha256"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cert, key, ca = create_certificate(args.hostname, args.directory, args.mode, args.force)
    print(f"cert : {cert}")
    print(f"key  : {key}")
    print(f"ca   : {ca}")
    context, _ = create_tls_context(args.hostname, args.directory, args.mode)
    print(f"context OK (min={context.minimum_version.name})")
