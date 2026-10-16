"""
Extração de dados de um post do site.

As informações vêm de várias camadas, da mais confiável para a menos confiável:
  1. Categorias do WordPress (tipo, áudio, qualidade, ano)
  2. Ficha técnica do post ("Título Traduzido: ...", "Tamanho: ...")
  3. Seção e rótulo de cada link ("DUAL ÁUDIO" / "WEB-DL 1080p (2.86 GB)" / "Episódio 01:")
  4. Nome do release dentro do magnet (dn=The.Drop.S01E01.1080p.WEB-DL...)
"""
import base64
import html
import re
import unicodedata
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from classificacao import tipo_corrigido


def sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def normalizar_espacos(texto):
    return re.sub(r"\s+", " ", (texto or "").replace("﻿", "")).strip()


# ==========================================
# MAGNET
# ==========================================
def normalizar_infohash(valor):
    """Converte o infohash para hex minúsculo (o site usa hex de 40 e base32 de 32 caracteres)."""
    valor = (valor or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", valor):
        return valor.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", valor):
        return base64.b32decode(valor.upper()).hex()
    return None


SPAM_RELEASE = [
    re.compile(r"[\[(][^\])]*(?:acesse|torrent|www\.)[^\])]*[\])]", re.I),
    re.compile(r"\bwww[ .][\w-]+[ .](?:com|net|org|tv|xyz|se|to|me|la|vip|info|biz)(?:[ .]br)?\b", re.I),
    re.compile(r"\b[\w-]*torrent[\w-]*[ .](?:com|net|org|tv|xyz|se|to|me|la|vip|info|biz)(?:[ .]br)?\b", re.I),
]


def limpar_release(nome):
    for regex in SPAM_RELEASE:
        nome = regex.sub(" ", nome)
    return normalizar_espacos(nome).strip(" -._")


def ler_magnet(href):
    """Devolve (infohash, nome_release, tamanho_bytes) ou None se o magnet for inválido."""
    href = html.unescape(href)
    query = parse_qs(urlparse(href).query)
    infohash = None
    for xt in query.get("xt", []):
        if xt.lower().startswith("urn:btih:"):
            infohash = normalizar_infohash(xt[9:])
            break
    if not infohash:
        return None
    nome = limpar_release((query.get("dn") or [""])[0])
    xl = (query.get("xl") or [""])[0]
    return infohash, nome, int(xl) if xl.isdigit() else None


# ==========================================
# ANÁLISE DE TEXTO (rótulos, nomes de release)
# ==========================================
RESOLUCOES = [
    (re.compile(r"2160p|\b4k\b|\buhd\b", re.I), "2160p"),
    (re.compile(r"1080p|full ?hd|\bfhd\b", re.I), "1080p"),
    (re.compile(r"720p", re.I), "720p"),
    (re.compile(r"480p|360p", re.I), "480p"),
]
FONTES = [
    (re.compile(r"\b(?:hd)?cam(?:rip)?\b|\bhd-?ts\b|telesync", re.I), "CAM"),
    (re.compile(r"web-?dl", re.I), "WEB-DL"),
    (re.compile(r"web-?rip", re.I), "WEBRip"),
    (re.compile(r"blu-?ray|bdrip|brrip|bdremux|\bbd\b", re.I), "BluRay"),
    (re.compile(r"hdtv", re.I), "HDTV"),
    (re.compile(r"dvd-?rip|\bdvdr?\b|dvdscr", re.I), "DVD"),
    (re.compile(r"\bweb\b", re.I), "WEB-DL"),
]
CODECS = [
    (re.compile(r"x265|h\.?265|hevc", re.I), "x265"),
    (re.compile(r"x264|h\.?264|\bavc\b", re.I), "x264"),
    (re.compile(r"xvid|divx", re.I), "XviD"),
]
CODECS_AUDIO = [
    (re.compile(r"true-?hd", re.I), "TrueHD"),
    (re.compile(r"dts[ ._-]?(?:hd|ma|x)\b", re.I), "DTS-HD"),
    (re.compile(r"\bdts\b", re.I), "DTS"),
    (re.compile(r"ddp|dd\+|e-?ac-?3|dolby digital plus", re.I), "DD+"),
    (re.compile(r"\bdd(?=[ ._-]?[257][ ._][01]|\b)|ac-?3|dolby digital", re.I), "AC3"),
    (re.compile(r"aac", re.I), "AAC"),
    (re.compile(r"flac", re.I), "FLAC"),
    (re.compile(r"\bopus\b", re.I), "Opus"),
    (re.compile(r"mp3", re.I), "MP3"),
]
RE_ATMOS = re.compile(r"atmos", re.I)
# Canais: 5.1 e 7.1 isolados; 2.0 só junto de um codec/termo de áudio (evita "versão 2.0")
RE_CANAIS = re.compile(
    r"(?<![\d.])([57])[ ._]1(?![\d])|"
    r"(?:ddp?\+?|e?-?ac-?3|aac|dts(?:-?hd)?(?:[ ._]ma)?|truehd|atmos|flac|opus|dual|[áa]udio|pcm|\bma)"
    r"[ ._-]?([257])[ ._]([01])(?![\d])|"
    r"\b([257])[ .]([01])\s*ch\b", re.I)
AUDIOS = [
    (re.compile(r"dual[ ._-]?[áa]udio|\bdual\b", re.I), "Dual Áudio"),
    (re.compile(r"nacional", re.I), "Nacional"),
    (re.compile(r"dublad|\bdub\b", re.I), "Dublado"),
    (re.compile(r"legendad|\bleg\b", re.I), "Legendado"),
]
RE_SXE = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})(?:[ ._-]?(?:-|E|-E)(\d{1,3}))?\b", re.I)
RE_NXN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
RE_SEASON_PACK = re.compile(r"\bS(\d{1,2})\b(?![ ._-]?E\d)", re.I)
RE_TEMPORADA = re.compile(r"(\d{1,2})\s*[ªa°º]?\s*Temporada", re.I)
RE_EPISODIO = re.compile(
    r"\b(?:Epis[óo]dios?|Ep\.?|Cap[íi]tulos?)\s*(\d{1,4})(?:\s*(?:a|ao|até|-|e|&)\s*(\d{1,4}))?", re.I)
RE_COMPLETA = re.compile(r"\bcomplet[ao]\b|\bcomplete\b", re.I)
RE_TAMANHO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(TB|GB|MB|GiB|MiB)\b", re.I)
MULTIPLICADOR = {"tb": 1024 ** 4, "gb": 1024 ** 3, "gib": 1024 ** 3, "mb": 1024 ** 2, "mib": 1024 ** 2}


def _primeiro(lista, texto):
    for regex, valor in lista:
        if regex.search(texto):
            return valor
    return None


def canais_audio(texto):
    m = RE_CANAIS.search(texto or "")
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)}.1"
    if m.group(2):
        return f"{m.group(2)}.{m.group(3)}"
    return f"{m.group(4)}.{m.group(5)}"


def codec_audio(texto):
    codec = _primeiro(CODECS_AUDIO, texto or "")
    if RE_ATMOS.search(texto or ""):
        codec = f"{codec} Atmos" if codec else "Atmos"
    return codec


def tamanho_em_bytes(texto):
    m = RE_TAMANHO.search(texto or "")
    if not m:
        return None
    return int(float(m.group(1).replace(",", ".")) * MULTIPLICADOR[m.group(2).lower()])


def analisar_texto(texto):
    """Extrai o que for possível de um texto livre (rótulo do link ou nome do release)."""
    texto = texto or ""
    d = {
        "resolucao": _primeiro(RESOLUCOES, texto),
        "fonte": _primeiro(FONTES, texto),
        "codec": _primeiro(CODECS, texto),
        "audio": _primeiro(AUDIOS, texto),
        "audio_codec": codec_audio(texto),
        "canais": canais_audio(texto),
        "tamanho_bytes": tamanho_em_bytes(texto),
        "temporada": None, "episodio": None, "episodio_fim": None, "completa": False,
    }
    m = RE_SXE.search(texto) or RE_NXN.search(texto)
    if m:
        d["temporada"], d["episodio"] = int(m.group(1)), int(m.group(2))
        if m.re is RE_SXE and m.group(3):
            d["episodio_fim"] = int(m.group(3))
    else:
        m = RE_EPISODIO.search(texto)
        if m:
            d["episodio"] = int(m.group(1))
            if m.group(2):
                d["episodio_fim"] = int(m.group(2))
        m = RE_SEASON_PACK.search(texto)
        if m:
            d["temporada"] = int(m.group(1))
            if d["episodio"] is None:
                d["completa"] = True
    if d["temporada"] is None:
        m = RE_TEMPORADA.search(texto)
        if m:
            d["temporada"] = int(m.group(1))
    if RE_COMPLETA.search(texto):
        d["completa"] = True
    return d


# ==========================================
# TÍTULO E FICHA TÉCNICA
# ==========================================
RE_CORTE_TITULO = re.compile(
    r"\s+(?:[–—|-]\s+)?(?=Torrent\b|Download\b|Baixar\b|\(\d{4}|Dual\b|Dublad|Legendad|Nacional\b|WEB[ -]?DL|WEB\b|"
    r"Blu-?Ray|BDRip|BRRip|HDTV|DVD|HDCAM|CAM\b|\d{3,4}p\b|4K\b|HD\b|\d{1,2}\s*[ªa°º]?\s*Temporada|Temporada|"
    r"Completa\b|S\d{2}E\d{2})",
    re.I)


RE_CORTE_FICHA = re.compile(r"\s+(?:[–—|-]\s+)?(?=Torrent\b|\d{1,2}\s*[ªa°º]?\s*Temporada|Completa\b)", re.I)


def limpar_titulo(titulo, ano=None, agressivo=True):
    """`agressivo` corta em qualquer termo de qualidade (para o título do post);
    sem ele, só remove 'Torrent'/'Temporada' (para o título da ficha, que já é limpo)."""
    titulo = normalizar_espacos(html.unescape(titulo or ""))
    titulo = re.sub(r"^(?:baixar|download)\s+", "", titulo, flags=re.I)
    titulo = (RE_CORTE_TITULO if agressivo else RE_CORTE_FICHA).split(titulo, maxsplit=1)[0]
    if ano:
        titulo = re.sub(rf"\s+{ano}$", "", titulo)
    return titulo.strip(" –—-|:,.")


ROTULOS_FICHA = {
    "titulo traduzido": "titulo", "titulo original": "titulo_original",
    "imdb": "imdb", "ano de lancamento": "ano", "lancamento": "ano", "ano": "ano",
    "genero": "generos", "formato": "formato", "qualidade": "qualidade",
    "idioma": "idioma", "audio": "idioma", "legenda": "legenda",
    "tamanho": "tamanho", "duracao": "duracao",
    "codec de audio": "codec_audio", "audio codec": "codec_audio", "canais de audio": "canais",
}


def ler_ficha(soup):
    """Lê a ficha técnica (pares 'Rótulo: valor' antes da sinopse)."""
    texto = soup.get_text("\n")
    texto = re.sub(r"[ \t\xa0﻿]+", " ", texto)
    texto = re.sub(r"\s*\n\s*:\s*", ": ", texto)
    texto = re.sub(r":\s*\n\s*", ": ", texto)
    ficha = {}
    for linha in texto.split("\n"):
        m = re.match(r"^\s*([^:\n]{2,30}?)\s*:\s*(.+?)\s*$", linha)
        if not m:
            continue
        chave = sem_acento(m.group(1)).lower().strip()
        if chave.startswith("sinopse"):
            break
        campo = ROTULOS_FICHA.get(chave)
        if campo and campo not in ficha:
            ficha[campo] = m.group(2)[:200]
    return ficha


# ==========================================
# CONTEXTO DE CADA LINK
# ==========================================
BLOCOS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "center", "td", "blockquote"}
TEXTOS_GENERICOS = re.compile(
    r"^(?:download(?: magnet| torrent)?|magnet(?:[ -]?link)?|baixar|clique aqui|torrent|link|"
    r"servidor(?: para download)?|download torrent)\s*:?$", re.I)
RE_SECAO = re.compile(r"dublad|legendad|dual|nacional|vers[ãa]o|original|portugu", re.I)
RE_FIM_BUSCA_SECAO = re.compile(r"sinopse|informa[çc][õo]es", re.I)


def _eh_magnet(tag):
    return isinstance(tag, Tag) and tag.name == "a" and tag.get("href", "").startswith("magnet:")


def _bloco(tag):
    pai = tag.parent
    while pai is not None and pai.name not in BLOCOS:
        pai = pai.parent
    return pai


def rotulo_da_linha(link):
    """
    Texto da 'linha' do link (do último <br> até ele, ignorando o texto de outros links).
    Se a linha estiver vazia, usa a linha de cima (rótulo acima do botão), desde que
    ela não pertença a outro link.
    """
    bloco = _bloco(link)
    if bloco is None:
        return ""
    linha, linha_anterior, linha_anterior_tem_link = [], "", False
    linha_tem_link = False
    for el in bloco.descendants:
        if el is link:
            break
        if isinstance(el, Tag) and el.name == "br":
            linha_anterior, linha_anterior_tem_link = " ".join(linha), linha_tem_link
            linha, linha_tem_link = [], False
        elif _eh_magnet(el):
            linha_tem_link = True
        elif isinstance(el, NavigableString) and not isinstance(el, Comment):
            if not any(_eh_magnet(p) for p in el.parents if p is not bloco):
                linha.append(str(el))
    rotulo = normalizar_espacos(" ".join(linha)).strip(" :|-")
    if not rotulo and not linha_anterior_tem_link:
        rotulo = normalizar_espacos(linha_anterior).strip(" :|-")
    if not rotulo:
        # Alguns posts colocam o rótulo no parágrafo anterior
        anterior = bloco.find_previous_sibling(list(BLOCOS))
        if anterior is not None and not anterior.find(_eh_magnet):
            texto = normalizar_espacos(anterior.get_text(" "))
            if len(texto) < 100 and (RE_TAMANHO.search(texto) or _primeiro(RESOLUCOES, texto)
                                     or RE_EPISODIO.search(texto)):
                rotulo = texto.strip(" :|-")
    return "" if TEXTOS_GENERICOS.match(rotulo) else rotulo


def secao_do_link(link):
    """Procura o cabeçalho de seção acima do link (ex: 'DUAL ÁUDIO', ':: LEGENDADO ::')."""
    for el in link.find_all_previous(["p", "h2", "h3", "h4", "h5", "div", "center", "strong", "b", "span"],
                                     limit=80):
        texto = normalizar_espacos(el.get_text(" "))
        if not texto or len(texto) > 60:
            continue
        if RE_FIM_BUSCA_SECAO.search(texto):
            break
        bloco = el if el.name in BLOCOS else _bloco(el)
        if bloco is not None and bloco.find(_eh_magnet):
            continue  # é o rótulo de outro link, não um cabeçalho
        if "assistir" in texto.lower():
            continue
        if RE_SECAO.search(texto):
            return texto.strip(" :")
    return ""


# ==========================================
# POST COMPLETO
# ==========================================
CATEGORIAS_TIPO = [
    ({"series", "serie", "_top series_"}, "Série"),
    ({"anime"}, "Anime"),
    ({"programas", "app", "aplicativos"}, "Software"),
    ({"pc", "xbox 360", "ps3", "ps2", "jogos"}, "Jogo"),
    ({"shows"}, "Show"),
    ({"filmes", "filme"}, "Filme"),
]


def _tipo(cats_norm, titulo_post, torrents):
    for nomes, tipo in CATEGORIAS_TIPO:
        if cats_norm & nomes:
            return tipo
    if re.search(r"\bcrack\b|\bkeygen\b|\bportable\b|x86|x64", titulo_post, re.I):
        return "Software"
    if RE_TEMPORADA.search(titulo_post) or sum(t["episodio"] is not None for t in torrents) > len(torrents) / 2:
        return "Série"
    return "Filme"


def _unico(valores):
    valores = {v for v in valores if v}
    return valores.pop() if len(valores) == 1 else None


def extrair_post(post_id, slug, titulo_post, conteudo_html, categorias, publicado=None, modificado=None,
                 capa=None):
    """
    Recebe os dados brutos de um post e devolve (post, torrents).
    `categorias` é a lista de NOMES das categorias do post.
    """
    titulo_post = normalizar_espacos(html.unescape(titulo_post))
    soup = BeautifulSoup(conteudo_html or "", "html.parser")
    ficha = ler_ficha(soup)
    cats_norm = {sem_acento(c).lower().strip() for c in categorias}

    # Informações da obra
    m_ano = re.search(r"\b(19\d{2}|20\d{2})\b", ficha.get("ano", ""))
    anos_cat = [c for c in categorias if re.fullmatch(r"(19|20)\d{2}", c.strip())]
    m_ano_titulo = re.search(r"\((19\d{2}|20\d{2})", titulo_post) or re.search(r"\b(19\d{2}|20\d{2})\b", titulo_post)
    ano = int(m_ano.group(1)) if m_ano else int(anos_cat[0]) if len(anos_cat) == 1 else \
        int(m_ano_titulo.group(1)) if m_ano_titulo else None

    imdb = None
    m = re.search(r"\d+(?:[.,]\d+)?", ficha.get("imdb", ""))
    if m:
        valor = float(m.group(0).replace(",", "."))
        imdb = valor if 0 < valor <= 10 else None

    titulo = limpar_titulo(ficha["titulo"], ano, agressivo=False) if ficha.get("titulo") else ""
    if not titulo:
        titulo = limpar_titulo(titulo_post, ano) or titulo_post
    titulo_original = limpar_titulo(ficha.get("titulo_original", ""), ano, agressivo=False) or None
    info_titulo = analisar_texto(titulo_post)
    # Áudio no nível do post (ficha e título): só usado quando o link não diz nada
    texto_audio_post = " ".join(ficha.get(c, "") for c in ("codec_audio", "canais", "formato", "idioma"))
    codec_audio_post = codec_audio(texto_audio_post) or info_titulo["audio_codec"]
    canais_post = canais_audio(texto_audio_post) or info_titulo["canais"]

    # Padrões vindos das categorias (só usados se forem únicos no post)
    audio_cat = _unico(_primeiro(AUDIOS, c) for c in categorias)
    res_cat = _unico(_primeiro(RESOLUCOES, c) for c in categorias)
    fonte_cat = _unico(_primeiro(FONTES, c) for c in categorias)

    torrents, vistos = [], set()
    for link in soup.find_all(_eh_magnet):
        magnet = ler_magnet(link["href"])
        if not magnet or magnet[0] in vistos:
            continue
        infohash, nome_release, xl = magnet
        vistos.add(infohash)

        texto_link = normalizar_espacos(link.get_text(" "))
        if TEXTOS_GENERICOS.match(texto_link):
            texto_link = ""
        rotulo = normalizar_espacos(f"{rotulo_da_linha(link)} {texto_link}")
        secao = secao_do_link(link)

        r = analisar_texto(rotulo)
        s = analisar_texto(secao)
        n = analisar_texto(nome_release)

        def escolher(campo, *extras):
            for valor in (r[campo], n[campo], *extras):
                if valor:
                    return valor
            return None

        torrents.append({
            "infohash": infohash,
            "magnet": html.unescape(link["href"]),
            "nome_release": nome_release or None,
            "rotulo": rotulo[:200] or None,
            "secao": secao or None,
            "audio": r["audio"] or s["audio"] or n["audio"] or audio_cat,
            "resolucao": escolher("resolucao", s["resolucao"], res_cat),
            "fonte": escolher("fonte", s["fonte"], _primeiro(FONTES, ficha.get("qualidade", "")), fonte_cat),
            "codec": escolher("codec"),
            "audio_codec": escolher("audio_codec", s["audio_codec"], codec_audio_post),
            "canais": escolher("canais", s["canais"], canais_post),
            "temporada": r["temporada"] or n["temporada"] or info_titulo["temporada"],
            "episodio": r["episodio"] if r["episodio"] is not None else n["episodio"],
            "episodio_fim": r["episodio_fim"] or n["episodio_fim"],
            "completa": r["completa"] or n["completa"],
            "tamanho_bytes": r["tamanho_bytes"] or xl,
        })

    # Sem tamanho por link e um único torrent: usa o tamanho da ficha
    if len(torrents) == 1 and not torrents[0]["tamanho_bytes"]:
        torrents[0]["tamanho_bytes"] = tamanho_em_bytes(ficha.get("tamanho", ""))

    tipo = _tipo(cats_norm, titulo_post, torrents)
    # O site cataloga alguns jogos, músicas e tutoriais como filme
    tipo = tipo_corrigido(tipo, titulo, normalizar_espacos(ficha.get("generos", "")), imdb,
                          normalizar_espacos(ficha.get("duracao", "")))
    if tipo not in ("Série", "Anime"):
        for t in torrents:
            t["temporada"] = t["episodio"] = t["episodio_fim"] = None
            t["completa"] = False

    if not capa:
        img = soup.find("img", src=re.compile(r"tmdb|imdb|\.jpe?g", re.I))
        capa = img["src"] if img else None

    post = {
        "id": post_id, "slug": slug, "titulo_post": titulo_post, "titulo": titulo,
        "titulo_original": titulo_original if titulo_original != titulo else None,
        "ano": ano, "tipo": tipo, "temporada": info_titulo["temporada"] if tipo in ("Série", "Anime") else None,
        "generos": normalizar_espacos(ficha.get("generos", "")) or None, "imdb": imdb,
        "duracao": normalizar_espacos(ficha.get("duracao", "")) or None, "capa": capa,
        "cam": any(t["fonte"] == "CAM" for t in torrents) and all(t["fonte"] in ("CAM", None) for t in torrents),
        "publicado": publicado, "modificado": modificado,
    }
    return post, torrents


def extrair_pagina_html(html_pagina):
    """Modo reserva (sem API): extrai id, slug, título, categorias e conteúdo de uma página de post."""
    soup = BeautifulSoup(html_pagina, "html.parser")
    shortlink = soup.find("link", rel="shortlink")
    m = re.search(r"[?&]p=(\d+)", shortlink["href"]) if shortlink else None
    canonical = soup.find("link", rel="canonical")
    artigo = soup.find("article") or soup
    titulo = artigo.find("h1")
    conteudo = artigo.find("div", class_="content")
    return {
        "id": int(m.group(1)) if m else None,
        "slug": urlparse(canonical["href"]).path.strip("/") if canonical else None,
        "titulo": titulo.get_text(" ", strip=True) if titulo else "",
        "categorias": [a.get_text(strip=True) for a in artigo.select(".category a")],
        "conteudo": str(conteudo) if conteudo else "",
    }
