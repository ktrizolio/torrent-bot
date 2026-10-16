"""
Consultas ao banco gerado pelo scraper (aberto sempre em modo somente leitura).

A busca aceita filtros misturados ao texto:
    /b duna 1080p dublado      -> resolução e áudio
    /b the boys s02            -> temporada 2      (s02e05 -> episódio 5)
    /b alien 4k filme          -> tipo
    /b oppenheimer cam         -> inclui versões gravadas no cinema (ocultas por padrão)
"""
import re
import sqlite3
import unicodedata
from contextlib import contextmanager

RESOLUCOES = {"4k": "2160p", "2160p": "2160p", "uhd": "2160p", "1080p": "1080p", "720p": "720p", "480p": "480p"}
AUDIOS = {
    "dublado": ("Dublado", "Dual Áudio", "Nacional"), "dub": ("Dublado", "Dual Áudio", "Nacional"),
    "dual": ("Dual Áudio",), "legendado": ("Legendado",), "leg": ("Legendado",), "nacional": ("Nacional",),
}
TIPOS = {"filme": "Filme", "filmes": "Filme", "serie": "Série", "series": "Série", "anime": "Anime"}


def _sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def interpretar(texto):
    """Separa o texto de busca dos filtros. Devolve (termos, filtros)."""
    termos, filtros = [], {}
    for palavra in texto.split():
        chave = _sem_acento(palavra).lower()
        if chave in RESOLUCOES:
            filtros["resolucao"] = RESOLUCOES[chave]
        elif chave in AUDIOS:
            filtros["audio"] = AUDIOS[chave]
        elif chave in TIPOS:
            filtros["tipo"] = TIPOS[chave]
        elif chave == "cam":
            filtros["cam"] = True
        elif m := re.fullmatch(r"s(\d{1,2})(?:e(\d{1,3}))?|t(\d{1,2})", chave):
            filtros["temporada"] = int(m.group(1) or m.group(3))
            if m.group(2):
                filtros["episodio"] = int(m.group(2))
        else:
            termos.append(palavra)
    return termos, filtros


def _sql_filtros_torrent(filtros, prefixo="t"):
    sql, params = [], []
    if "resolucao" in filtros:
        sql.append(f"{prefixo}.resolucao = ?")
        params.append(filtros["resolucao"])
    if "audio" in filtros:
        sql.append(f"{prefixo}.audio IN ({','.join('?' * len(filtros['audio']))})")
        params.extend(filtros["audio"])
    if not filtros.get("cam"):
        sql.append(f"({prefixo}.fonte IS NULL OR {prefixo}.fonte != 'CAM')")
    if "episodio" in filtros:
        sql.append(f"({prefixo}.episodio = ? OR ({prefixo}.episodio <= ? AND {prefixo}.episodio_fim >= ?) "
                   f"OR {prefixo}.completa = 1)")
        params.extend([filtros["episodio"]] * 3)
    return sql, params


class Banco:
    def __init__(self, caminho):
        self.caminho = caminho

    @contextmanager
    def _conectar(self):
        conn = sqlite3.connect(f"file:{self.caminho}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def buscar(self, texto, limite=60):
        """Busca posts (obras/temporadas) que tenham pelo menos um torrent compatível com os filtros."""
        termos, filtros = interpretar(texto)
        if not termos:
            return [], filtros
        # Cada palavra vira um prefixo obrigatório: "senhor aneis" -> "senhor"* "aneis"*
        consulta = " ".join('"' + t.replace('"', "") + '"*' for t in termos if t.replace('"', ""))
        if not consulta:
            return [], filtros
        where_t, params_t = _sql_filtros_torrent(filtros)
        where_p, params_p = [], []
        if "tipo" in filtros:
            where_p.append("p.tipo = ?")
            params_p.append(filtros["tipo"])
        if "temporada" in filtros:
            where_p.append("(p.temporada = ? OR t.temporada = ?)")
            params_p.extend([filtros["temporada"]] * 2)
        where = " AND ".join(["1=1", *where_t, *where_p])
        sql = f"""
            WITH achados AS MATERIALIZED (
                SELECT rowid AS id, bm25(busca, 10.0, 8.0, 1.0) AS relevancia
                FROM busca WHERE busca MATCH ?
            )
            SELECT p.id, p.titulo, p.ano, p.tipo, p.temporada, p.imdb, COUNT(t.id) AS n_torrents,
                   MIN(a.relevancia) AS relevancia
            FROM achados a
            JOIN posts p ON p.id = a.id
            JOIN torrents t ON t.post_id = p.id
            WHERE {where}
            GROUP BY p.id
            ORDER BY CASE WHEN p.titulo LIKE ? OR p.titulo_original LIKE ? THEN 0
                          WHEN p.titulo LIKE ? || '%' THEN 1 ELSE 2 END,
                     relevancia, p.ano DESC, p.temporada
            LIMIT ?
        """
        frase = " ".join(termos)
        with self._conectar() as conn:
            try:
                linhas = conn.execute(sql, [consulta, *params_t, *params_p, frase, frase, frase, limite]).fetchall()
            except sqlite3.OperationalError as e:
                if "fts5" in str(e):
                    return [], filtros  # consulta inválida (ex: só símbolos)
                raise
        return linhas, filtros

    def post(self, post_id):
        with self._conectar() as conn:
            return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()

    def torrents_do_post(self, post_id, filtros=None):
        where, params = _sql_filtros_torrent(filtros or {})
        sql = f"""
            SELECT * FROM torrents t
            WHERE t.post_id = ? {''.join(' AND ' + w for w in where)}
            ORDER BY t.completa DESC, t.temporada, t.episodio, t.ordem
        """
        with self._conectar() as conn:
            linhas = conn.execute(sql, [post_id, *params]).fetchall()
            if not linhas and filtros:  # filtros não batem com nada: mostra tudo
                return self.torrents_do_post(post_id)
            return linhas

    def torrent(self, torrent_id):
        with self._conectar() as conn:
            return conn.execute("""
                SELECT t.*, p.titulo, p.ano, p.tipo AS tipo_post, p.temporada AS temporada_post
                FROM torrents t JOIN posts p ON p.id = t.post_id WHERE t.id = ?
            """, (torrent_id,)).fetchone()

    def torrent_por_hash(self, infohash):
        with self._conectar() as conn:
            return conn.execute("""
                SELECT t.*, p.titulo, p.ano, p.tipo AS tipo_post, p.temporada AS temporada_post
                FROM torrents t JOIN posts p ON p.id = t.post_id WHERE t.infohash = ?
            """, (infohash.lower(),)).fetchone()

    def ano_serie(self, titulo):
        """Ano de estreia: o menor ano entre os posts de temporadas da mesma série (o Jellyfin busca por ele)."""
        with self._conectar() as conn:
            linha = conn.execute("SELECT MIN(ano) FROM posts WHERE titulo = ? AND tipo IN ('Série', 'Anime')",
                                 (titulo,)).fetchone()
            return linha[0] if linha else None
