"""
Camada de rede: sessão HTTP com novas tentativas automáticas e resolução de DNS
via Cloudflare (DNS-over-HTTPS) apenas para os domínios configurados.
"""
import json
import logging
import socket
import threading
import time
import urllib.request

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DOH_URL = "https://cloudflare-dns.com/dns-query?name={host}&type=A"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_getaddrinfo_original = socket.getaddrinfo
_cache_dns = {}
_lock_dns = threading.Lock()


def _resolver_doh(host):
    """Consulta o IP do host na Cloudflare (DNS-over-HTTPS). Resultado fica em cache."""
    with _lock_dns:
        if host in _cache_dns:
            return _cache_dns[host]
        req = urllib.request.Request(DOH_URL.format(host=host),
                                     headers={"accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = json.load(resp)
        ips = [r["data"] for r in dados.get("Answer", []) if r.get("type") == 1]
        if not ips:
            raise OSError(f"DNS Cloudflare não retornou IP para {host}")
        _cache_dns[host] = ips[0]
        log.info("DNS Cloudflare: %s -> %s", host, ips[0])
        return ips[0]


def ativar_dns_cloudflare(hosts):
    """Faz o Python resolver os hosts informados pela Cloudflare (só neste processo)."""
    hosts = {h.lower() for h in hosts}

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, str) and host.lower() in hosts:
            host = _resolver_doh(host.lower())
        return _getaddrinfo_original(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo


def criar_sessao():
    sessao = requests.Session()
    sessao.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    adaptador = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    sessao.mount("https://", adaptador)
    sessao.mount("http://", adaptador)
    return sessao


class Cliente:
    """Sessão compartilhada com limite de ritmo (intervalo mínimo entre requisições)."""

    def __init__(self, intervalo=0.3, timeout=60):
        self.sessao = criar_sessao()
        self.intervalo = intervalo
        self.timeout = timeout
        self._lock = threading.Lock()
        self._ultima = 0.0

    def get(self, url, **kwargs):
        with self._lock:
            espera = self._ultima + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.monotonic()
        resp = self.sessao.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp
