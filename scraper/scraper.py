"""
Scraper de sites de torrents feitos em WordPress (o endereço vem de SITE_URL).

Uso:
    python scraper.py                 # atualização incremental (só posts novos/alterados)
    python scraper.py --completo      # recoleta tudo
    python scraper.py --modo sitemap  # modo reserva, caso a API do WordPress seja desativada
    python scraper.py --sem-doh       # não usar a DNS da Cloudflare
    python scraper.py --repetir-horas 6   # fica rodando e atualiza a cada 6 horas (modo servidor)
    python scraper.py --horario-diario 03:00   # fica rodando e atualiza todo dia às 03:00
"""
import argparse
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse

import requests

import banco
import rede
from extracao import extrair_pagina_html, extrair_post

# ==========================================
# CONFIGURAÇÃO
# ==========================================
# Todas podem ser trocadas por variáveis de ambiente (útil no Docker)
SITE = os.environ.get("SITE_URL", "").rstrip("/")
DB_PADRAO = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados", "torrents.db"))
USAR_DNS_CLOUDFLARE = os.environ.get("USAR_DNS_CLOUDFLARE", "1") != "0"  # domínio bloqueado em alguns provedores
INTERVALO_HORAS = float(os.environ.get("INTERVALO_HORAS", "0"))  # 0 = roda uma vez e sai
HORARIO_DIARIO = os.environ.get("HORARIO_DIARIO", "").strip()     # ex: "03:00" (substitui o intervalo)
POSTS_POR_PAGINA = 100         # máximo permitido pela API do WordPress
THREADS = 4                    # requisições simultâneas
INTERVALO = 0.3                # segundos mínimos entre requisições

CAMPOS_API = "id,slug,title,content,categories,date_gmt,modified_gmt,meta"
NS_SITEMAP = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

log = logging.getLogger("scraper")


# ==========================================
# MODO API (principal)
# ==========================================
def baixar_categorias(cliente):
    categorias, pagina, total = [], 1, 1
    while pagina <= total:
        resp = cliente.get(f"{SITE}/wp-json/wp/v2/categories",
                           params={"per_page": 100, "page": pagina, "_fields": "id,name,slug"})
        total = int(resp.headers.get("X-WP-TotalPages", 1))
        categorias.extend(resp.json())
        pagina += 1
    return categorias


def processar_post_api(p, nomes_categorias):
    capa = (p.get("meta") or {}).get("fifu_image_url") or None
    post, torrents = extrair_post(
        post_id=p["id"], slug=p["slug"], titulo_post=p["title"]["rendered"],
        conteudo_html=p["content"]["rendered"],
        categorias=[nomes_categorias.get(c, "") for c in p["categories"]],
        publicado=p.get("date_gmt"), modificado=p.get("modified_gmt"), capa=capa)
    return post, torrents, p["categories"]


def coletar_api(conn, cliente, completo):
    log.info("Baixando categorias...")
    categorias = baixar_categorias(cliente)
    banco.salvar_categorias(conn, categorias)
    conn.commit()
    nomes = {c["id"]: c["name"] for c in categorias}
    log.info("%d categorias.", len(categorias))

    desde = None if completo else banco.ler_estado(conn, "api_modificado_ate")
    params = {"per_page": POSTS_POR_PAGINA, "orderby": "id", "order": "asc", "_fields": CAMPOS_API}
    if desde:
        # Margem de 1 dia: a API compara no fuso do site, e reprocessar alguns posts é barato
        margem = datetime.fromisoformat(desde[:19]) - timedelta(days=1)
        params["modified_after"] = margem.strftime("%Y-%m-%dT%H:%M:%S")
        log.info("Modo incremental: posts alterados desde %s (UTC, com 1 dia de margem).", desde)
    else:
        log.info("Coleta completa.")

    url = f"{SITE}/wp-json/wp/v2/posts"
    primeira = cliente.get(url, params=dict(params, page=1))
    total_paginas = int(primeira.headers.get("X-WP-TotalPages", 0))
    total_posts = int(primeira.headers.get("X-WP-Total", 0))
    log.info("%d posts em %d páginas da API.", total_posts, total_paginas)
    if total_posts == 0:
        log.info("Nada novo.")
        return True

    def baixar(pagina):
        return cliente.get(url, params=dict(params, page=pagina)).json()

    maior_modificado = desde or ""
    posts_salvos = torrents_salvos = 0
    falhas = []

    def gravar_lote(pagina, lote):
        nonlocal maior_modificado, posts_salvos, torrents_salvos
        for p in lote:
            try:
                post, torrents, cats = processar_post_api(p, nomes)
            except Exception as e:  # um post com HTML estranho não pode derrubar a coleta
                banco.registrar_falha(conn, f"{SITE}/?p={p.get('id')}", f"extração: {e!r}")
                continue
            banco.salvar_post(conn, post, torrents, cats)
            posts_salvos += 1
            torrents_salvos += len(torrents)
            maior_modificado = max(maior_modificado, p.get("modified_gmt") or "")
        banco.limpar_falha(conn, f"{url}?page={pagina}")
        conn.commit()

    gravar_lote(1, primeira.json())
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futuros = {executor.submit(baixar, n): n for n in range(2, total_paginas + 1)}
        for i, futuro in enumerate(as_completed(futuros), start=2):
            pagina = futuros[futuro]
            try:
                gravar_lote(pagina, futuro.result())
            except Exception as e:
                falhas.append(pagina)
                banco.registrar_falha(conn, f"{url}?page={pagina}", e)
                conn.commit()
                log.warning("Falha na página %d da API: %s", pagina, e)
            print(f"\rPáginas: {i}/{total_paginas} | posts: {posts_salvos} | torrents: {torrents_salvos}",
                  end="", flush=True)
    print()

    if falhas:
        log.warning("%d páginas falharam (%s). A data de controle NÃO foi avançada; "
                    "rode novamente para tentar de novo.", len(falhas), falhas[:10])
        return False
    banco.gravar_estado(conn, "api_modificado_ate", maior_modificado)
    banco.gravar_estado(conn, "ultima_coleta", banco.agora())
    conn.commit()
    log.info("Concluído: %d posts e %d torrents gravados.", posts_salvos, torrents_salvos)
    return True


# ==========================================
# MODO SITEMAP (reserva)
# ==========================================
def ler_sitemap(cliente, url):
    raiz = ET.fromstring(cliente.get(url).content)
    return [(e.findtext("sm:loc", namespaces=NS_SITEMAP), e.findtext("sm:lastmod", namespaces=NS_SITEMAP))
            for e in raiz if e.find("sm:loc", NS_SITEMAP) is not None]


def coletar_sitemap(conn, cliente, completo):
    indice = ler_sitemap(cliente, f"{SITE}/sitemap_index.xml")
    # Só os sitemaps de posts: os de tag e categoria não têm links
    sitemaps = [loc for loc, _ in indice if re.search(r"/post-sitemap\d*\.xml$", loc)]
    log.info("%d sitemaps de posts.", len(sitemaps))

    paginas = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        for resultado in executor.map(lambda u: ler_sitemap(cliente, u), sitemaps):
            paginas.extend(resultado)

    conhecidos = {} if completo else {unquote(s).lower(): m for s, m in conn.execute("SELECT slug, modificado FROM posts")}
    pendentes = []
    for loc, lastmod in paginas:
        slug = unquote(urlparse(loc).path.strip("/")).lower()
        modificado = (lastmod or "")[:19]
        if slug not in conhecidos or (modificado and modificado > (conhecidos[slug] or "")):
            pendentes.append((loc, modificado))
    pendentes.extend((u, "") for (u,) in conn.execute("SELECT url FROM falhas WHERE url NOT LIKE '%wp-json%'"))
    log.info("%d páginas no sitemap, %d para visitar.", len(paginas), len(pendentes))

    def baixar(item):
        loc, modificado = item
        dados = extrair_pagina_html(cliente.get(loc).text)
        if not dados["id"]:
            raise ValueError("id do post não encontrado na página")
        post, torrents = extrair_post(dados["id"], dados["slug"] or urlparse(loc).path.strip("/"),
                                      dados["titulo"], dados["conteudo"], dados["categorias"],
                                      modificado=modificado or None)
        return post, torrents

    ok = True
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futuros = {executor.submit(baixar, item): item[0] for item in pendentes}
        for i, futuro in enumerate(as_completed(futuros), start=1):
            loc = futuros[futuro]
            try:
                post, torrents = futuro.result()
                banco.salvar_post(conn, post, torrents)
                banco.limpar_falha(conn, loc)
            except Exception as e:
                ok = False
                banco.registrar_falha(conn, loc, e)
            if i % 50 == 0:
                conn.commit()
            print(f"\rPáginas: {i}/{len(pendentes)}", end="", flush=True)
    print()
    conn.commit()
    return ok


# ==========================================
# PRINCIPAL
# ==========================================
def resumo(conn):
    print("\n========== RESUMO DO BANCO ==========")
    print("Posts por tipo:", dict(conn.execute("SELECT tipo, COUNT(*) FROM posts GROUP BY tipo ORDER BY 2 DESC")))
    total = conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0]
    print(f"Torrents: {total}")
    for campo in ("audio", "resolucao", "fonte", "tamanho_bytes"):
        n = conn.execute(f"SELECT COUNT(*) FROM torrents WHERE {campo} IS NOT NULL").fetchone()[0]
        print(f"  {campo:14} preenchido em {n * 100 // max(total, 1)}%")
    falhas = conn.execute("SELECT COUNT(*) FROM falhas").fetchone()[0]
    if falhas:
        print(f"Falhas pendentes: {falhas} (veja a tabela 'falhas'; serão tentadas de novo)")


def executar(db, modo, completo):
    """Uma coleta completa ou incremental. Devolve True se terminou sem falhas."""
    conn = banco.conectar(db)
    cliente = rede.Cliente(intervalo=INTERVALO)
    try:
        if modo == "api":
            try:
                return coletar_api(conn, cliente, completo)
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code not in (401, 403, 404):
                    raise
                log.warning("API do WordPress indisponível (%s). Usando o modo sitemap.", e.response.status_code)
        return coletar_sitemap(conn, cliente, completo)
    finally:
        conn.commit()
        resumo(conn)
        banco.finalizar(conn)


def main():
    if not SITE:
        raise SystemExit("Defina SITE_URL (no .env) com o endereço do site.")
    parser = argparse.ArgumentParser(description="Scraper de torrents")
    parser.add_argument("--completo", action="store_true", help="ignora a última coleta e baixa tudo")
    parser.add_argument("--modo", choices=("api", "sitemap"), default="api")
    parser.add_argument("--db", default=DB_PADRAO, help=f"arquivo do banco (padrão: {DB_PADRAO})")
    parser.add_argument("--sem-doh", action="store_true", help="não usar a DNS da Cloudflare")
    parser.add_argument("--repetir-horas", type=float, default=INTERVALO_HORAS,
                        help="repete a coleta a cada N horas sem parar (0 = roda uma vez)")
    parser.add_argument("--horario-diario", default=HORARIO_DIARIO, metavar="HH:MM",
                        help="roda todo dia neste horário (tem prioridade sobre --repetir-horas)")
    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO,
                        datefmt="%d/%m %H:%M:%S")
    if USAR_DNS_CLOUDFLARE and not args.sem_doh:
        rede.ativar_dns_cloudflare([urlparse(SITE).hostname])

    if args.horario_diario:
        hora, minuto = (int(x) for x in args.horario_diario.split(":"))
    elif args.repetir_horas <= 0:
        try:
            ok = executar(args.db, args.modo, args.completo)
        except KeyboardInterrupt:
            print("\nInterrompido. O que já foi gravado está salvo; rode de novo para continuar.")
            ok = False
        sys.exit(0 if ok else 1)

    # Modo servidor: roda para sempre. Erros (site fora do ar, sem internet) não derrubam o processo.
    if args.horario_diario:
        log.info("Modo servidor: coleta todo dia às %02d:%02d.", hora, minuto)
    else:
        log.info("Modo servidor: coleta a cada %g hora(s).", args.repetir_horas)
    completo = args.completo
    primeira = True
    while True:
        if args.horario_diario:
            agora = datetime.now()
            proxima = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if proxima <= agora:
                proxima += timedelta(days=1)
            log.info("Próxima coleta: %s", proxima.strftime("%d/%m %H:%M"))
            time.sleep((proxima - agora).total_seconds())
        elif not primeira:
            proxima = datetime.now() + timedelta(hours=args.repetir_horas)
            log.info("Próxima coleta: %s", proxima.strftime("%d/%m %H:%M"))
            time.sleep(args.repetir_horas * 3600)
        primeira = False
        try:
            executar(args.db, args.modo, completo)
            completo = False
        except Exception:
            log.exception("Erro na coleta; tentando de novo no próximo ciclo.")


if __name__ == "__main__":
    main()
