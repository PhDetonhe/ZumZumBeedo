"""Export the verified HTTPS trust anchor to the ESP32 (Python >= 3.13)."""
import argparse
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="https://zum-zum-beedo.vercel.app")
    args = parser.parse_args()
    url = urlsplit(args.server)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        parser.error("Use o endereço HTTPS público do BeeGuard, sem credenciais.")
    if not hasattr(ssl.SSLSocket, "get_verified_chain"):
        parser.error("Este utilitário exige Python 3.13 ou mais recente.")
    context = ssl.create_default_context()
    with socket.create_connection((url.hostname, url.port or 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=url.hostname) as connection:
            # The operating system validates both chain and hostname before export.
            chain = connection.get_verified_chain()
    certificate = ssl.DER_cert_to_PEM_cert(chain[-1])
    target = Path(__file__).resolve().parents[1] / "firmware/BeeGuardWiFi/trusted_root_ca.h"
    target.write_text(
        '#pragma once\n// Verified trust anchor for ' + url.hostname + '\n'
        'static const char BEEGUARD_ROOT_CA[] = R"BGCERT(\n' + certificate + ')BGCERT";\n',
        encoding="ascii",
    )
    print("Certificado público verificado salvo em", target)


if __name__ == "__main__":
    main()
