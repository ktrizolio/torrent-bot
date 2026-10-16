"""
Gera as bibliotecas "Catálogo Filmes" e "Catálogo Séries" do Jellyfin a partir do banco do scraper.

    /catalogo/Filmes/Título (Ano)/  Título (Ano).mkv (link para .stub/stub.mkv), movie.nfo, poster.jpg, catalogo.json
    /catalogo/Series/Título (Ano)/  tvshow.nfo, poster.jpg, catalogo.json, Season XX/Título SXXE01.mkv (um por temporada)

Posts com o mesmo título (e ano, nos filmes) viram um item só; catalogo.json guarda os ids dos posts.
O plugin "Catálogo (baixar)" do Jellyfin lê o catalogo.json para listar as versões no botão Baixar.

Uso (no container do bot):  python catalogo.py [--limite N] [--sem-capas]
"""
import argparse
import json
import logging
import os
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

import nomes

DB_PATH = os.environ.get("DB_PATH", "/app/dados/torrents.db")
PASTA = os.environ.get("PASTA_CATALOGO", "/catalogo")
STUB = os.path.join(PASTA, ".stub", "stub.mkv")
TIPOS = {"Filmes": ("Filme",), "Series": ("Série", "Anime")}

log = logging.getLogger("catalogo")


def _tag(nome, valor):
    return f"  <{nome}>{escape(str(valor))}</{nome}>\n" if valor not in (None, "") else ""


def _minutos(duracao):
    if not duracao:
        return None
    h, _, resto = duracao.partition("h")
    try:
        return int(h.strip() or 0) * 60 + int("".join(c for c in resto if c.isdigit()) or 0)
    except ValueError:
        return None


def nfo(raiz, p, ano, versoes):
    generos = "".join(_tag("genre", g.strip()) for g in (p["generos"] or "").split(",") if g.strip())
    linha = "Para baixar: botão ☁ na página do título"
    if versoes:
        linha += " · " + ", ".join(versoes[:6])
    return (f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<{raiz}>\n"
            + _tag("title", p["titulo"]) + _tag("originaltitle", p["titulo_original"]) + _tag("year", ano)
            + _tag("rating", p["imdb"]) + (_tag("runtime", _minutos(p["duracao"])) if raiz == "movie" else "")
            + _tag("tagline", linha) + generos + _tag("tag", "Catálogo") + f"</{raiz}>\n")


def grupos(conn, tipos, limite):
    """Posts agrupados: filmes por (título, ano); séries por título (todas as temporadas juntas)."""
    marcas = ",".join("?" * len(tipos))
    linhas = conn.execute(f"""
        SELECT p.* FROM posts p
        WHERE p.tipo IN ({marcas}) AND EXISTS (SELECT 1 FROM torrents t WHERE t.post_id = p.id)
        ORDER BY p.publicado DESC
    """, tipos).fetchall()
    serie = tipos != ("Filme",)
    saida = {}
    for p in linhas:
        chave = p["titulo"].strip().lower() if serie else (p["titulo"].strip().lower(), p["ano"])
        saida.setdefault(chave, []).append(p)
        if limite and len(saida) > limite:
            del saida[chave]
            break
    return list(saida.values())


def baixar_capa(url, destino):
    """Devolve True se baixou. Nunca propaga a exceção: um HTTPError guardado no futuro mantém o socket
    aberto, e com milhares de capas mortas o processo esgotava os arquivos abertos (Errno 24)."""
    partes = urllib.parse.urlsplit(url.replace("/w342/", "/w500/"))
    url = urllib.parse.urlunsplit(partes._replace(path=urllib.parse.quote(partes.path)))  # acentos no nome
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(pedido, timeout=20) as resposta, open(destino + ".tmp", "wb") as arquivo:
            shutil.copyfileobj(resposta, arquivo)
        os.replace(destino + ".tmp", destino)
        return True
    except urllib.error.HTTPError as erro:
        erro.close()
    except Exception:
        pass
    if os.path.exists(destino + ".tmp"):
        os.remove(destino + ".tmp")
    open(destino + ".falhou", "w").close()  # capa morta: não tenta de novo toda madrugada
    return False


def _escrever(caminho, texto):
    atual = None
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            atual = f.read()
    if atual != texto:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(texto)


def _link(destino):
    if not os.path.exists(destino):
        pasta = os.path.dirname(destino)
        if not os.path.isdir(pasta):
            os.makedirs(pasta)
            os.chmod(pasta, 0o777)
        os.link(STUB, destino)  # link: não ocupa espaço


def gerar_secao(conn, secao, limite, capas, executor):
    base = os.path.join(PASTA, secao)
    os.makedirs(base, exist_ok=True)
    serie = secao == "Series"
    usados, novos, capas_pend = set(), 0, []
    for posts in grupos(conn, TIPOS[secao], limite):
        p = posts[0]  # o post mais recente dá título, capa e dados
        ano = min((x["ano"] for x in posts if x["ano"]), default=None) if serie else p["ano"]
        nome = nomes.com_ano(p["titulo"], ano)
        if not nome:
            continue
        unico, n = nome, 2
        while unico.lower() in usados:
            unico = f"{nome} - {n}"
            n += 1
        usados.add(unico.lower())
        pasta = os.path.join(base, unico)
        if not os.path.isdir(pasta):
            os.makedirs(pasta)
            os.chmod(pasta, 0o777)  # o Jellyfin (outro usuário) grava metadados aqui
            novos += 1
        ids = [x["id"] for x in posts]
        marcas = ",".join("?" * len(ids))
        versoes = sorted({" ".join(v for v in (r["resolucao"], r["audio"]) if v) for r in conn.execute(
            f"SELECT DISTINCT resolucao, audio FROM torrents WHERE post_id IN ({marcas})", ids)} - {""})
        if serie:
            temporadas = sorted({r[0] for r in conn.execute(
                f"SELECT DISTINCT COALESCE(t.temporada, p.temporada, 1) FROM torrents t JOIN posts p ON p.id = t.post_id "
                f"WHERE t.post_id IN ({marcas})", ids)} | set())
            titulo_arquivo = nomes.nome_seguro(p["titulo"])
            for temp in temporadas or [1]:
                _link(os.path.join(pasta, f"Season {temp:02d}", f"{titulo_arquivo} S{temp:02d}E01.mkv"))
            _escrever(os.path.join(pasta, "tvshow.nfo"), nfo("tvshow", p, ano, versoes))
        else:
            _link(os.path.join(pasta, f"{unico}.mkv"))
            _escrever(os.path.join(pasta, "movie.nfo"), nfo("movie", p, ano, versoes))
        _escrever(os.path.join(pasta, "catalogo.json"), json.dumps({"posts": ids, "tipo": secao}))
        capa = os.path.join(pasta, "poster.jpg")
        url = next((x["capa"] for x in posts if x["capa"]), None)
        if capas and url and not os.path.exists(capa) and not os.path.exists(capa + ".falhou"):
            capas_pend.append(executor.submit(baixar_capa, url, capa))
    falhas = sum(not futuro.result() for futuro in capas_pend)
    # Remove itens que saíram do catálogo (só pastas criadas por este gerador)
    removidos = 0
    for nome in os.listdir(base):
        pasta = os.path.join(base, nome)
        if nome.lower() not in usados and os.path.isfile(os.path.join(pasta, "catalogo.json")):
            shutil.rmtree(pasta)
            removidos += 1
    log.info("Catálogo %s: %d itens (%d novos, %d removidos), %d capas baixadas, %d falharam.",
             secao, len(usados), novos, removidos, len(capas_pend) - falhas, falhas)


def gerar(limite=None, capas=True):
    if not os.path.exists(STUB):
        raise SystemExit(f"Falta o vídeo vazio em {STUB}")
    # Itens do protótipo, gerados direto na raiz: saem (agora tudo fica em Filmes/ e Series/)
    for nome in os.listdir(PASTA):
        pasta = os.path.join(PASTA, nome)
        if nome not in ("Filmes", "Series", ".stub") and os.path.isfile(os.path.join(pasta, "catalogo.json")):
            shutil.rmtree(pasta)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    with ThreadPoolExecutor(max_workers=8) as executor:
        for secao in ("Filmes", "Series"):
            gerar_secao(conn, secao, limite, capas, executor)
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    args = argparse.ArgumentParser()
    args.add_argument("--limite", type=int)
    args.add_argument("--sem-capas", action="store_true")
    a = args.parse_args()
    gerar(a.limite, not a.sem_capas)
