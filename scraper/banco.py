"""
Banco de dados SQLite do scraper.

posts       -> uma obra (filme) ou uma temporada (série), igual a um post do site
torrents    -> cada link magnet, identificado pelo infohash (único)
categorias  -> categorias do WordPress (gêneros, áudio, qualidade, ano...)
busca       -> índice de busca (FTS5) sem acentos: título, título original e nomes dos releases
"""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY,      -- id do post no WordPress
    slug            TEXT NOT NULL,
    titulo_post     TEXT NOT NULL,            -- título original do post, sem limpeza
    titulo          TEXT NOT NULL,            -- título limpo
    titulo_original TEXT,
    ano             INTEGER,
    tipo            TEXT NOT NULL,            -- Filme, Série, Anime, Show, Software, Jogo
    temporada       INTEGER,
    generos         TEXT,
    imdb            REAL,
    duracao         TEXT,
    capa            TEXT,
    cam             INTEGER NOT NULL DEFAULT 0,   -- 1 = só tem versões gravadas no cinema
    publicado       TEXT,
    modificado      TEXT,
    coletado_em     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_tipo ON posts(tipo);
CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug);

CREATE TABLE IF NOT EXISTS torrents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    infohash        TEXT NOT NULL UNIQUE,     -- hex minúsculo, 40 caracteres
    magnet          TEXT NOT NULL,
    nome_release    TEXT,
    rotulo          TEXT,                     -- texto do site ao lado do link
    secao           TEXT,                     -- cabeçalho do site acima do link
    audio           TEXT,                     -- Dual Áudio, Dublado, Legendado, Nacional
    resolucao       TEXT,                     -- 2160p, 1080p, 720p, 480p
    fonte           TEXT,                     -- WEB-DL, WEBRip, BluRay, HDTV, DVD, CAM
    codec           TEXT,
    audio_codec     TEXT,                     -- AAC, AC3, DD+, DTS, TrueHD (+ Atmos)
    canais          TEXT,                     -- 2.0, 5.1, 7.1
    temporada       INTEGER,
    episodio        INTEGER,
    episodio_fim    INTEGER,                  -- para pacotes "Episódios 01 ao 06"
    completa        INTEGER NOT NULL DEFAULT 0,
    tamanho_bytes   INTEGER,
    ordem           INTEGER NOT NULL          -- posição do link no post
);
CREATE INDEX IF NOT EXISTS idx_torrents_post ON torrents(post_id);

CREATE TABLE IF NOT EXISTS categorias (
    id      INTEGER PRIMARY KEY,
    nome    TEXT NOT NULL,
    slug    TEXT
);
CREATE TABLE IF NOT EXISTS post_categorias (
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    categoria_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, categoria_id)
);

CREATE TABLE IF NOT EXISTS falhas (
    url         TEXT PRIMARY KEY,
    erro        TEXT,
    tentativas  INTEGER NOT NULL DEFAULT 1,
    ultima      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS estado (
    chave   TEXT PRIMARY KEY,
    valor   TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS busca USING fts5(
    titulo, titulo_original, releases,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def conectar(caminho):
    conn = sqlite3.connect(caminho)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(SCHEMA)
    # Bancos criados antes destas colunas existirem
    colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(torrents)")}
    for coluna in ("audio_codec", "canais"):
        if coluna not in colunas:
            conn.execute(f"ALTER TABLE torrents ADD COLUMN {coluna} TEXT")
    return conn


def finalizar(conn):
    """Deixa o banco num único arquivo (sem -wal/-shm), pronto para copiar para o servidor do bot."""
    conn.execute("PRAGMA optimize")
    conn.execute("INSERT INTO busca(busca) VALUES('optimize')")
    conn.commit()
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.OperationalError:
        pass  # o bot está lendo neste instante; o banco continua íntegro em modo WAL
    conn.close()


def ler_estado(conn, chave, padrao=None):
    linha = conn.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
    return linha[0] if linha else padrao


def gravar_estado(conn, chave, valor):
    conn.execute("INSERT OR REPLACE INTO estado (chave, valor) VALUES (?, ?)", (chave, valor))


def salvar_categorias(conn, categorias):
    conn.executemany("INSERT OR REPLACE INTO categorias (id, nome, slug) VALUES (:id, :name, :slug)", categorias)


def salvar_post(conn, post, torrents, categoria_ids=()):
    """Grava (ou substitui) um post e seus torrents. Não faz commit."""
    p = dict(post, cam=int(post["cam"]), coletado_em=agora())
    conn.execute("""
        INSERT INTO posts (id, slug, titulo_post, titulo, titulo_original, ano, tipo, temporada, generos,
                           imdb, duracao, capa, cam, publicado, modificado, coletado_em)
        VALUES (:id, :slug, :titulo_post, :titulo, :titulo_original, :ano, :tipo, :temporada, :generos,
                :imdb, :duracao, :capa, :cam, :publicado, :modificado, :coletado_em)
        ON CONFLICT(id) DO UPDATE SET
            slug=excluded.slug, titulo_post=excluded.titulo_post, titulo=excluded.titulo,
            titulo_original=excluded.titulo_original, ano=excluded.ano, tipo=excluded.tipo,
            temporada=excluded.temporada, generos=excluded.generos, imdb=excluded.imdb,
            duracao=excluded.duracao, capa=excluded.capa, cam=excluded.cam, publicado=excluded.publicado,
            modificado=excluded.modificado, coletado_em=excluded.coletado_em
    """, p)

    conn.execute("DELETE FROM torrents WHERE post_id = ?", (post["id"],))
    for ordem, t in enumerate(torrents):
        # O mesmo torrent pode aparecer em dois posts (ex: um "Listão"): fica no primeiro
        conn.execute("""
            INSERT OR IGNORE INTO torrents (post_id, infohash, magnet, nome_release, rotulo, secao, audio,
                resolucao, fonte, codec, audio_codec, canais, temporada, episodio, episodio_fim, completa,
                tamanho_bytes, ordem)
            VALUES (:post_id, :infohash, :magnet, :nome_release, :rotulo, :secao, :audio, :resolucao, :fonte,
                :codec, :audio_codec, :canais, :temporada, :episodio, :episodio_fim, :completa, :tamanho_bytes, :ordem)
        """, dict(t, post_id=post["id"], completa=int(t["completa"]), ordem=ordem))

    if categoria_ids:
        conn.execute("DELETE FROM post_categorias WHERE post_id = ?", (post["id"],))
        conn.executemany("INSERT OR IGNORE INTO post_categorias (post_id, categoria_id) VALUES (?, ?)",
                         [(post["id"], c) for c in categoria_ids])

    releases = " ".join(t["nome_release"] for t in torrents if t["nome_release"])
    conn.execute("DELETE FROM busca WHERE rowid = ?", (post["id"],))
    conn.execute("INSERT INTO busca (rowid, titulo, titulo_original, releases) VALUES (?, ?, ?, ?)",
                 (post["id"], post["titulo"], post["titulo_original"] or "", releases))


def registrar_falha(conn, url, erro):
    conn.execute("""
        INSERT INTO falhas (url, erro, ultima) VALUES (?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET erro=excluded.erro, tentativas=tentativas+1, ultima=excluded.ultima
    """, (url, str(erro)[:300], agora()))


def limpar_falha(conn, url):
    conn.execute("DELETE FROM falhas WHERE url = ?", (url,))
