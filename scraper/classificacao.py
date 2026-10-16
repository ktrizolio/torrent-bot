"""
Corrige posts que o site cataloga como filme mas são jogos, músicas ou tutoriais.

Usado pela extração (posts novos) e por reclassificar_banco() (posts já gravados).
Os sinais fortes valem sempre ("(PC)", versão "v1.2.3", "Kbps"); os fracos (gêneros de jogo ou de música)
só quando o post não tem dados de filme (sem nota IMDb e sem duração).
"""
import re
import sqlite3

JOGO_TITULO = re.compile(
    r"\((PC|PS[1-5]|PSP|PS ?VITA|XBOX[^)]*|X360|SWITCH|WII[^)]*|MAC|ANDROID|NDS|3DS|GBA)\)"
    r"|\bv\d+(\.\d+){1,}\b"
    r"|\bdlc\b|\bfifa\s*\d{2}\b|\bpes\s*20\d\d\b|\bnba\s*2k\d\d|\bwwe\s*2k\d\d"
    r"|\broms\b|\bemulador\b|jogos? em link|list[aã]o games|\bvideogame\b", re.I)
JOGO_GENERO = re.compile(
    r"jogos? de pc|tiro em 1|\brpg\b|simulador|simula[cç][aã]o|sandbox|mundo aberto|point\s*&\s*click|visual novel"
    r"|survival horror|twin stick|plataforma|hack and slash|\bmmo|estrat[eé]gia|\bcorrida\b|stealth|roguelike"
    r"|metroidvania|beat.?em.?up|\bpuzzle\b", re.I)
MUSICA_TITULO = re.compile(
    r"\d{3}\s*kbps|trilha sonora|\bost\b|soundtrack|\bplaylist\b|\bhits\b|\btop\s*\d+\b|colet[aâ]nea|discografia"
    r"|\bcd\b|[aá]lbum|mixtape", re.I)
GENEROS_MUSICA = (r"dance|pop|rock|sertanejo|funk|eletr[oô]nica|pagode|forr[oó]|gospel|hip hop|rap|mpb|samba|reggae"
                  r"|ax[eé]|house|trance|country|jazz|blues|metal|trilha sonora|soundtrack|ost")
MUSICA_GENERO = re.compile(rf"^\s*({GENEROS_MUSICA})(\s*[,|/]\s*({GENEROS_MUSICA}))*\s*$", re.I)
TUTORIAL_TITULO = re.compile(
    r"^\s*como\s|utorrent|magnet link|\btutoria(l|is)\b|\bd[uú]vidas\b|kd a legenda|aprendendo a|\bwindows\b"
    r"|\boffice\b|ativador|\bcurso\b|apostila|\bebook\b", re.I)


def tipo_corrigido(tipo, titulo, generos, imdb, duracao):
    """Devolve o tipo certo para um post marcado como "Filme" (ou o próprio tipo, se nada indicar outro)."""
    if tipo != "Filme":
        return tipo
    titulo, generos = titulo or "", generos or ""
    sem_dados = imdb is None and not (duracao or "").strip()
    if JOGO_TITULO.search(titulo) or re.search(r"jogos? de pc", generos, re.I):
        return "Jogo"
    if sem_dados and re.search(r"\b(repack|crack|codex|skidrow|fitgirl|razor1911)\b|\bPC\b", titulo, re.I):
        return "Jogo"
    if MUSICA_TITULO.search(titulo) and (sem_dados or re.search(GENEROS_MUSICA, generos, re.I)):
        return "Música"
    if sem_dados and TUTORIAL_TITULO.search(titulo):
        return "Software"
    if sem_dados and not re.search(r"cole[cç][aã]o|trilogia", titulo, re.I):
        if JOGO_GENERO.search(generos):
            return "Jogo"
        if MUSICA_GENERO.search(generos):
            return "Música"
    return tipo


def reclassificar_banco(caminho_db, aplicar=False):
    """Recalcula o tipo dos posts de filme já gravados. Devolve [(id, titulo, novo_tipo)]."""
    conn = sqlite3.connect(caminho_db)
    mudancas = []
    for pid, titulo, generos, imdb, duracao in conn.execute(
            "SELECT id, titulo, generos, imdb, duracao FROM posts WHERE tipo = 'Filme'"):
        novo = tipo_corrigido("Filme", titulo, generos, imdb, duracao)
        if novo != "Filme":
            mudancas.append((pid, titulo, novo))
    if aplicar and mudancas:
        conn.executemany("UPDATE posts SET tipo = ? WHERE id = ?", [(n, pid) for pid, _, n in mudancas])
        conn.commit()
    conn.close()
    return mudancas


if __name__ == "__main__":
    import sys
    from collections import Counter
    aplicar = "--aplicar" in sys.argv
    mud = reclassificar_banco(sys.argv[1], aplicar)
    print(Counter(n for _, _, n in mud), "(aplicado)" if aplicar else "(simulação)")
    for pid, titulo, novo in mud:
        print(f"{novo:8} {pid:7} {titulo}")
