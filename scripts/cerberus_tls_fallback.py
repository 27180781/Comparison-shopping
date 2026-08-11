"""Let the Cerberus chains through when the portal refuses AUTH TLS.

il-supermarket-scraper reaches url.retail.publishedprices.co.il with
ftplib.FTP_TLS. That constructor logs in, which issues AUTH TLS, and the
portal answers `504 Command not implemented for that parameter` — so
RAMI_LEVY and OSHER_AD both scrape zero files. See PHASE0-FINDINGS F-13.

Plain FTP against the same host is fine: it connects and returns
`230 Password Ok, User logged in`. So swap the module-level FTP_TLS name in
the library for a factory that tries TLS first and falls back to plain FTP
only when the server refuses the AUTH negotiation. Two call sites use that
name and nothing else does, which makes the patch small and reversible.

On confidentiality: the fallback sends the login in the clear. That is
acceptable here and nowhere else — the usernames ship inside the library
source (RamiLevi, osherad), the passwords are empty by design, and the files
are price data the retailers are legally required to publish. Do not reuse
this shim for a host where any of that stops being true.

This is a workaround, not a fix. The fix belongs upstream; until then the
alternative is losing two of twelve chains, one of them Rami Levy.

    import cerberus_tls_fallback
    cerberus_tls_fallback.install()
"""

from __future__ import annotations

from ftplib import FTP, FTP_TLS, error_perm, error_proto

# The server's refusal. Anything else is a real error and must propagate.
AUTH_REFUSED_CODES = ("500", "502", "504", "534")


class PlainFTP(FTP):
    """FTP that tolerates being asked for TLS operations it cannot perform.

    ftplib's FTP_TLS callers may call auth()/prot_p(); answering them with a
    success code keeps the library's code path unchanged rather than requiring
    it to know which transport it got.
    """

    def auth(self):  # noqa: D102 - matches FTP_TLS
        return "234 AUTH not available, continuing unencrypted"

    def prot_p(self):  # noqa: D102 - matches FTP_TLS
        return "200 PROT not available, continuing unencrypted"

    def prot_c(self):  # noqa: D102 - matches FTP_TLS
        return "200 PROT not available, continuing unencrypted"


def connect_with_fallback(host, user="", passwd="", *args, **kwargs):
    """Try FTPS; drop to plain FTP when the server refuses the AUTH command."""
    try:
        return FTP_TLS(host, user, passwd, *args, **kwargs)
    except (error_perm, error_proto) as exc:
        if not str(exc).startswith(AUTH_REFUSED_CODES):
            raise
        return PlainFTP(host, user, passwd, *args, **kwargs)


def install() -> bool:
    """Patch the library. Returns False when the library is not importable."""
    try:
        from il_supermarket_scarper.utils import connection
    except ImportError:
        return False

    if getattr(connection, "_cerberus_fallback_installed", False):
        return True

    connection.FTP_TLS = connect_with_fallback
    connection._cerberus_fallback_installed = True
    return True
