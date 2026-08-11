#!/usr/bin/env python3
"""Diagnose Cerberus (FTP) access, which covers RAMI_LEVY and OSHER_AD.

The library talks to url.retail.publishedprices.co.il with ftplib.FTP_TLS,
whose constructor logs in and therefore issues AUTH TLS. A `504 Command not
implemented for that parameter` there means the AUTH negotiation was refused,
and there are three very different causes:

  1. the server does not offer AUTH TLS at all,
  2. something on the path (consumer routers love an FTP ALG) is rewriting the
     control connection and breaking the TLS upgrade,
  3. the local network stack (WSL, VPN) is interfering.

This walks the connection one step at a time and prints what the server says,
which separates those cases. FEAT is the decisive one: if it advertises AUTH
TLS but AUTH TLS then fails, the refusal is not coming from the server.

Usage:
    python scripts/phase0_check_cerberus.py
    python scripts/phase0_check_cerberus.py --username osherad
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from ftplib import FTP, FTP_TLS, error_perm, error_proto

HOST = "url.retail.publishedprices.co.il"
TIMEOUT = 30


def step(label: str) -> None:
    print(f"\n── {label} " + "─" * max(0, 60 - len(label)))


def check_plain(host: str, username: str, password: str) -> list[str]:
    """Plain FTP: reachability, login, advertised features, and a data transfer."""
    features: list[str] = []

    step("1. plain FTP connect")
    try:
        ftp = FTP(host, timeout=TIMEOUT)
    except (OSError, socket.timeout) as exc:
        print(f"FAIL  cannot reach {host}:21 — {exc}")
        print("      Port 21 is blocked outbound, or DNS failed.")
        return features
    print(f"OK    banner: {ftp.getwelcome()}")

    step("2. plain FTP login (no TLS)")
    try:
        print(f"OK    {ftp.login(username, password)}")
    except (error_perm, error_proto) as exc:
        print(f"FAIL  login rejected — {exc}")
        return features

    # FEAT must come after login here: this server answers 530 Not logged in
    # to a pre-auth FEAT, which would make "AUTH TLS not advertised" meaningless.
    step("3. FEAT — what the server says it supports (post-login)")
    try:
        raw = ftp.sendcmd("FEAT")
        print(raw)
        features = [line.strip().upper() for line in raw.splitlines()[1:-1]]
    except (error_perm, error_proto) as exc:
        print(f"FAIL  server rejected FEAT — {exc}")

    advertises_tls = sorted(f for f in features if f.startswith("AUTH"))
    print(f"\n      AUTH mechanisms advertised: {advertises_tls or 'none'}")

    # The library sets this, so match it: without it ftplib dials whatever
    # address the server reports in its PASV reply, which is a black hole when
    # the server sits behind NAT. A timeout here without the flag proves
    # nothing about the network.
    step("4. data connection (PASV) with trust_server_pasv_ipv4_address")
    ftp.trust_server_pasv_ipv4_address = True
    try:
        names = ftp.nlst()
        print(f"OK    listing works — {len(names)} entries")
        print(f"      first few: {names[:5]}")
    except (error_perm, error_proto, OSError, socket.timeout) as exc:
        print(f"FAIL  listing failed — {exc}")
        print("      Control channel is fine but the data channel is not.")
        print("      Typical causes: passive-mode ports blocked outbound, or an")
        print("      FTP ALG in the router rewriting the PASV reply.")
    finally:
        try:
            ftp.quit()
        except (OSError, error_proto):
            pass

    return features


def check_auth_mechanisms(host: str) -> None:
    """Probe AUTH TLS and AUTH SSL directly, before login.

    Some servers accept the AUTH negotiation pre-auth even when they refuse
    FEAT, so this can distinguish "refuses TLS" from "refuses FEAT".
    """
    step("5. AUTH probes on a fresh connection")
    for mechanism in ("TLS", "SSL"):
        try:
            ftp = FTP(host, timeout=TIMEOUT)
        except (OSError, socket.timeout) as exc:
            print(f"FAIL  AUTH {mechanism}: cannot connect — {exc}")
            continue
        try:
            print(f"OK    AUTH {mechanism} -> {ftp.sendcmd('AUTH ' + mechanism)}")
        except (error_perm, error_proto) as exc:
            print(f"FAIL  AUTH {mechanism} -> {exc}")
        finally:
            try:
                ftp.close()
            except OSError:
                pass


def check_tls(host: str, username: str, password: str) -> None:
    """FTPS, the way the library does it."""
    step("6. FTP_TLS — exactly what the library does")
    try:
        ftps = FTP_TLS(host, username, password, timeout=TIMEOUT)
        ftps.trust_server_pasv_ipv4_address = True
        print("OK    AUTH TLS accepted and login succeeded")
        try:
            ftps.prot_p()
            names = ftps.nlst()
            print(f"OK    listing works — {len(names)} entries")
        except (error_perm, error_proto, OSError) as exc:
            print(f"WARN  connected but listing failed — {exc}")
        finally:
            try:
                ftps.quit()
            except (OSError, error_proto):
                pass
    except (error_perm, error_proto) as exc:
        print(f"FAIL  {exc}")
        if str(exc).startswith("504"):
            print(
                "      504 on AUTH TLS. Cross-check against FEAT above:\n"
                "      advertised     -> something on the path is interfering\n"
                "                        (router FTP ALG, VPN, WSL networking)\n"
                "      not advertised -> the server really is plain FTP only"
            )
    except ssl.SSLError as exc:
        print(f"FAIL  TLS handshake failed — {exc}")
    except (OSError, socket.timeout) as exc:
        print(f"FAIL  connection error — {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST)
    parser.add_argument(
        "--username",
        default="RamiLevi",
        help="Cerberus username (RamiLevi, osherad). Password is empty by design.",
    )
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    print(f"host     : {args.host}")
    print(f"username : {args.username}")
    print(f"password : {'(empty)' if not args.password else '(set)'}")

    check_plain(args.host, args.username, args.password)
    check_auth_mechanisms(args.host)
    check_tls(args.host, args.username, args.password)

    print("\nWhat to read: step 3 says whether the server offers TLS at all,")
    print("step 4 whether data transfers work, step 5 whether AUTH is refused")
    print("outright. Together they separate a server limitation from a")
    print("middlebox on the path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
