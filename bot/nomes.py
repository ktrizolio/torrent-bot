"""
Nomes de pasta e arquivo no padrão do Jellyfin.

    Filmes: /Media/Filmes/Título (Ano)/Título (Ano).mkv
    Séries: /Media/Series/Título (Ano)/Season 01/Título S01E06.mkv

O título e o ano vêm do banco do scraper (já limpos). limpar_release() é a reserva para quando só há o
nome do release (ex: "[www.Site.org] - Coyote.vs.Acme.2026.1080p.DCP.DDP5.1.H264-AOC").
"""
import os
import re
import unicodedata

PASTA_FILMES = os.environ.get("PASTA_FILMES", "/Media/Filmes")
PASTA_SERIES = os.environ.get("PASTA_SERIES", "/Media/Series")

EXT_VIDEO = {"mkv", "mp4", "avi", "m4v", "ts", "m2ts", "wmv", "mov", "mpg", "mpeg", "webm"}
EXT_LEGENDA = {"srt", "ass", "ssa", "sub", "idx", "vtt", "sup"}
EXT_LIXO = {"url", "txt", "nfo", "exe", "lnk", "html", "htm", "website", "db", "torrent", "bat", "scr"}

PROIBIDOS = re.compile(r'[<>"/\\|?*\x00-\x1f]')
ABREVIACOES = {"vs", "mr", "mrs", "ms", "dr", "sr", "jr", "st", "vol", "pt", "no"}

RE_EPISODIO = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})((?:-?E\d{1,3}|-\d{1,3})*)(?!\d)", re.I)
RE_EPISODIO_X = re.compile(r"\b(\d{1,2})x(\d{2,3})\b", re.I)
RE_ANO = re.compile(r"(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)")
RE_SAMPLE = re.compile(r"\bsample\b", re.I)

# Tudo o que aparece depois do título num nome de release
TAGS = re.compile(r"""\b(
    2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10?\+?|hdr|dv|dovi|sdr|imax|
    web[ ._-]?dl|web[ ._-]?rip|webrip|web|bluray|blu[ ._-]?ray|bdrip|brrip|bdremux|remux|dcp|hdtv|hdrip|dvdrip|dvd|
    hdcam|cam|ts|tc|r5|
    x264|x265|h[ ._]?264|h[ ._]?265|hevc|avc|xvid|divx|av1|10bit|8bit|
    ddp?[ ._]?\d[ ._]\d|dd\+?|e?ac3|aac\d?(?:[ ._]\d)?|dts(?:[ ._-]?hd)?|truehd|atmos|flac|mp3|opus|\d[ ._]\d|
    dual(?:[ ._]?audio)?|dublado|dub|legendado|leg|nacional|multi|japanese|korean|chinese|french|spanish|
    german|italian|english|portuguese|subbed|dubbed|
    proper|repack|extended|unrated|uncut|remastered|complete|completa|temporada|season|internal|limited
)\b""", re.I | re.X)
IDIOMAS_FIM = re.compile(r"(\s+(dual|dublado|legendado|nacional|japanese|korean|chinese|french|spanish|german|"
                         r"italian|english|portuguese|multi))+\s*$", re.I)
PREFIXO_SITE = re.compile(r"^\s*(\[[^\]]*\](\([^)]*\))?|www\.[\w-]+\.\w+|@\w+)\s*[-–—:|]*\s*", re.I)

IDIOMAS_LEGENDA = [
    (re.compile(r"\b(pt[-_ .]?br|ptbr|brazil|brazilian|portugu[eê]s[-_ .]?br)\b", re.I), "pt-BR"),
    (re.compile(r"\b(pt|por|portuguese|portugu[eê]s)\b", re.I), "pt"),
    (re.compile(r"\b(en|eng|english|ingl[eê]s)\b", re.I), "en"),
    (re.compile(r"\b(es|spa|spanish|espanhol)\b", re.I), "es"),
]


def nome_seguro(texto):
    """Nome válido em compartilhamento de rede: sem < > : " / \\ | ? *, dois-pontos vira ' - '."""
    texto = unicodedata.normalize("NFC", texto or "")
    texto = re.sub(r"\s*:\s*", " - ", texto)
    texto = PROIBIDOS.sub(" ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto.rstrip(". ")  # Windows não aceita nome terminado em ponto ou espaço


def com_ano(titulo, ano):
    titulo = nome_seguro(titulo)
    return f"{titulo} ({ano})" if ano else titulo


def _pontos_para_espacos(texto):
    """Coyote.vs.Acme -> Coyote vs. Acme; Uma.Batalha.Após.a.Outra -> Uma Batalha Após a Outra."""
    if " " in texto.strip() and "." not in texto.replace("...", ""):
        return texto
    partes = re.split(r"[._]+", texto)
    saida = []
    for i, p in enumerate(partes):
        if not p:
            continue
        if p.lower() in ABREVIACOES and i < len(partes) - 1:
            p += "."
        saida.append(p)
    return " ".join(saida)


def codigo_episodio(nome):
    """Devolve (temporada, episodio, episodio_fim) de 'S01E06', 'S01E01-02', 'S01E01E02' ou '1x06'."""
    m = RE_EPISODIO.search(nome or "")
    if m:
        numeros = [int(m.group(2))] + [int(n) for n in re.findall(r"\d{1,3}", m.group(3))]
        fim = max(numeros) if len(numeros) > 1 and max(numeros) > numeros[0] else None
        return int(m.group(1)), numeros[0], fim
    m = RE_EPISODIO_X.search(nome or "")
    if m:
        return int(m.group(1)), int(m.group(2)), None
    return None


def texto_episodio(temporada, episodio, fim=None):
    texto = f"S{temporada:02d}E{episodio:02d}"
    return f"{texto}-E{fim:02d}" if fim else texto


def limpar_release(nome):
    """Nome de release ou pasta -> (título, ano ou None, (temporada, episodio, fim) ou None)."""
    nome = os.path.basename((nome or "").rstrip("/\\"))
    base, ext = os.path.splitext(nome)
    if ext[1:].lower() in EXT_VIDEO | EXT_LEGENDA:
        nome = base
    while True:  # prefixos de site, às vezes repetidos
        novo = PREFIXO_SITE.sub("", nome, count=1)
        if novo == nome:
            break
        nome = novo
    episodio = codigo_episodio(nome)
    corte = len(nome)
    if episodio:
        corte = min(corte, (RE_EPISODIO.search(nome) or RE_EPISODIO_X.search(nome)).start())
    # Ano: o último que vem antes das tags (2001.A.Space.Odyssey.1968 -> 1968), nunca no início do título
    ano = None
    for m in RE_ANO.finditer(nome[:corte] if episodio else nome):
        if m.start() == 0 or re.match(r"\s*\(?\s*$", nome[:m.start()]):
            continue
        ano = int(m.group(1))
        corte = min(corte, m.start())
    if not episodio and ano is None:
        tag = TAGS.search(nome)
        if tag and tag.start() > 0:
            corte = tag.start()
    titulo = nome[:corte]
    titulo = re.sub(r"[\[\(]\s*$", "", titulo)
    titulo = _pontos_para_espacos(titulo)
    titulo = re.sub(r"[\[\]()]", " ", titulo)
    titulo = IDIOMAS_FIM.sub("", titulo)
    titulo = re.sub(r"\s+", " ", titulo).strip(" -–—_.")
    return nome_seguro(titulo), ano, episodio


def idioma_legenda(nome):
    for padrao, codigo in IDIOMAS_LEGENDA:
        if padrao.search(os.path.splitext(os.path.basename(nome))[0]):
            return codigo
    return None


def destino(tipo, titulo, ano):
    """Pasta de destino no qBittorrent: filme -> pasta do filme; série -> pasta da série."""
    if tipo in ("Série", "Anime"):
        return f"{PASTA_SERIES}/{com_ano(titulo, ano)}"
    return f"{PASTA_FILMES}/{com_ano(titulo, ano)}"


def _ext(caminho):
    return os.path.splitext(caminho)[1][1:].lower()


def planejar(arquivos, tipo, titulo, ano, temporada=None, episodio=None, episodio_fim=None):
    """
    arquivos: lista de (indice, caminho relativo no torrent, tamanho).
    Devolve (renomear, ignorar): renomear = [(caminho antigo, caminho novo)], ignorar = [índices sem download].
    Os caminhos novos são relativos à pasta de destino (destino()).
    """
    serie = tipo in ("Série", "Anime")
    nome_base = com_ano(titulo, ano) if not serie else nome_seguro(titulo)
    renomear, ignorar, usados = [], [], set()

    def livre(caminho):
        raiz, ext = os.path.splitext(caminho)
        n = 2
        while caminho.lower() in usados:
            caminho = f"{raiz} - {n}{ext}"
            n += 1
        usados.add(caminho.lower())
        return caminho

    videos = [a for a in arquivos if _ext(a[1]) in EXT_VIDEO]
    for indice, caminho, tamanho in arquivos:
        ext = _ext(caminho)
        if ext in EXT_LIXO or (ext in EXT_VIDEO and RE_SAMPLE.search(caminho) and tamanho < 500 * 1024 ** 2):
            ignorar.append(indice)
    reais = [a for a in videos if a[0] not in ignorar]

    if not serie:
        principal = max(reais, key=lambda a: a[2]) if reais else None
        for indice, caminho, tamanho in reais:
            if indice == (principal[0] if principal else None):
                novo = f"{nome_base}.{_ext(caminho)}"
            else:  # extras ficam numa subpasta que o Jellyfin entende como extras
                novo = f"extras/{os.path.basename(caminho)}"
            renomear.append((caminho, livre(novo)))
        for indice, caminho, tamanho in arquivos:
            if _ext(caminho) in EXT_LEGENDA and indice not in ignorar:
                lingua = idioma_legenda(caminho)
                novo = f"{nome_base}.{lingua}.{_ext(caminho)}" if lingua else f"{nome_base}.{_ext(caminho)}"
                renomear.append((caminho, livre(novo)))
        return renomear, ignorar

    # Série: cada episódio em Season XX/Título SxxEyy.ext, pelo código no nome do arquivo
    codigos = {}
    for indice, caminho, tamanho in reais:
        cod = codigo_episodio(os.path.basename(caminho)) or codigo_episodio(caminho)
        if not cod and len(reais) == 1 and episodio is not None:
            cod = (temporada or 1, episodio, episodio_fim)
        if cod:
            codigos[indice] = cod
            novo = f"Season {cod[0]:02d}/{nome_base} {texto_episodio(*cod)}.{_ext(caminho)}"
        else:
            pasta = f"Season {temporada:02d}/" if temporada else ""
            novo = f"{pasta}{os.path.basename(caminho)}"
        renomear.append((caminho, livre(novo)))
    for indice, caminho, tamanho in arquivos:
        if _ext(caminho) not in EXT_LEGENDA or indice in ignorar:
            continue
        cod = codigo_episodio(os.path.basename(caminho)) or codigo_episodio(caminho)
        if not cod and len(codigos) == 1:
            cod = next(iter(codigos.values()))
        if not cod:
            continue
        lingua = idioma_legenda(caminho)
        meio = f".{lingua}" if lingua else ""
        novo = f"Season {cod[0]:02d}/{nome_base} {texto_episodio(*cod)}{meio}.{_ext(caminho)}"
        renomear.append((caminho, livre(novo)))
    return renomear, ignorar
