"""
Bot do Telegram: busca no banco do scraper e envia o torrent escolhido ao qBittorrent.

Comandos:
    /b <texto> [filtros]   busca (ex: /b duna 1080p dublado, /b the boys s02)
    /status                downloads enviados pelo bot
    /start                 ajuda

Configuração por variáveis de ambiente (veja .env.example).
"""
import asyncio
import hmac
import html
import json
import logging
import os
import re
import threading
import urllib.request
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from qbittorrentapi import Client, Conflict409Error, LoginFailed
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import nomes
from consultas import Banco

try:  # facilita rodar fora do Docker com um arquivo .env
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

# ==========================================
# CONFIGURAÇÃO
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# BOT_PUBLICO=1 libera o bot para qualquer pessoa (cada uma vê só os próprios downloads no /status)
BOT_PUBLICO = os.environ.get("BOT_PUBLICO", "0").strip() == "1"
USUARIOS_AUTORIZADOS = {int(u) for u in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if u}
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados", "torrents.db"))

QBIT_HOST = os.environ.get("QBIT_HOST", "localhost")
QBIT_PORT = int(os.environ.get("QBIT_PORT", "8080"))
QBIT_USER = os.environ.get("QBIT_USERNAME", "admin")
QBIT_PASS = os.environ.get("QBIT_PASSWORD")

CATEGORIAS_QBIT = {  # tipo no banco -> categoria no qBittorrent
    "Filme": os.environ.get("QBIT_CATEGORIA_FILMES", "Filmes"),
    "Série": os.environ.get("QBIT_CATEGORIA_SERIES", "Séries"),
    "Anime": os.environ.get("QBIT_CATEGORIA_ANIMES", "Animes"),
}
CATEGORIA_OUTROS = os.environ.get("QBIT_CATEGORIA_OUTROS", "Outros")
PASTAS_QBIT = {  # pasta de destino opcional por categoria
    CATEGORIAS_QBIT["Filme"]: os.environ.get("QBIT_PASTA_FILMES", ""),
    CATEGORIAS_QBIT["Série"]: os.environ.get("QBIT_PASTA_SERIES", ""),
    CATEGORIAS_QBIT["Anime"]: os.environ.get("QBIT_PASTA_ANIMES", ""),
}
TAG_BOT = "telegram"
TAG_AVISADO = "telegram-avisado"
TAG_RENOMEAR = "renomear"     # aguardando a lista de arquivos para renomear no padrão do Jellyfin
TIPOS_MIDIA = ("Filme", "Série", "Anime")
TAG_JELLYFIN = "jellyfin"     # pedido feito pelo Catálogo do Jellyfin

# API para o plugin do Catálogo do Jellyfin (só na rede local, com chave compartilhada)
CATALOGO_PORTA = int(os.environ.get("CATALOGO_PORTA", "8099"))
CATALOGO_TOKEN = os.environ.get("CATALOGO_TOKEN", "")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")  # avisos de progresso aos usuários
TAG_AVISO_50 = "jf-aviso50"
TAG_AVISO_100 = "jf-aviso100"
HORA_CATALOGO = os.environ.get("HORA_CATALOGO", "00:40")  # depois da coleta da meia-noite

RESULTADOS_POR_PAGINA = 8
EPISODIOS_POR_PAGINA = 40
MAX_VERSOES_DIRETAS = 12      # acima disso, séries mostram primeiro a grade de episódios
INTERVALO_AVISOS = 60         # segundos entre verificações de downloads concluídos

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

banco = Banco(DB_PATH)
ICONES = {"Filme": "🎬", "Série": "📺", "Anime": "🍥", "Show": "🎤", "Software": "💾", "Jogo": "🎮"}


# ==========================================
# QBITTORRENT
# ==========================================
def conectar_qbit():
    try:
        cliente = Client(host=QBIT_HOST, port=QBIT_PORT, username=QBIT_USER, password=QBIT_PASS,
                         REQUESTS_ARGS={"timeout": 20})
        cliente.auth_log_in()
        return cliente
    except LoginFailed:
        log.error("qBittorrent: usuário ou senha incorretos.")
    except Exception as e:
        log.error("qBittorrent: falha na conexão: %s", e)
    return None


def tag_usuario_jellyfin(usuario):
    return "jf-" + re.sub(r"[^\w.-]", "_", usuario or "desconhecido")[:40]


def adicionar_torrent(torrent, chat_id, usuario_jellyfin=None):
    """Roda em thread separada (a biblioteca do qBittorrent não é assíncrona)."""
    qbt = conectar_qbit()
    if not qbt:
        return "erro_conexao"
    if qbt.torrents_info(torrent_hashes=torrent["infohash"]):
        return "ja_existe"
    categoria = CATEGORIAS_QBIT.get(torrent["tipo_post"], CATEGORIA_OUTROS)
    try:
        qbt.torrents_create_category(name=categoria, save_path=PASTAS_QBIT.get(categoria) or None)
    except Conflict409Error:
        pass  # categoria já existe
    extras = {}
    if chat_id is not None:
        tags = [TAG_BOT, f"tg-{chat_id}"]
    else:
        tags = [TAG_BOT, TAG_JELLYFIN, tag_usuario_jellyfin(usuario_jellyfin)]
    if torrent["tipo_post"] in TIPOS_MIDIA and torrent["titulo"]:
        # Pasta no padrão do Jellyfin já no download, sem a subpasta com nome de release
        destino = nomes.destino(torrent["tipo_post"], torrent["titulo"], ano_da_obra(torrent))
        if torrent["tipo_post"] == "Filme" and any(t.save_path.rstrip("/") == destino for t in qbt.torrents_info()):
            return "ja_baixando"  # outra versão do mesmo filme já está no qBittorrent
        extras = {"save_path": destino, "content_layout": "NoSubfolder", "use_auto_torrent_management": False}
        tags.append(TAG_RENOMEAR)
    resposta = qbt.torrents_add(urls=torrent["magnet"], category=categoria, tags=tags, **extras)
    return "ok" if resposta == "Ok." else f"qBittorrent respondeu: {resposta}"


def ano_da_obra(torrent):
    if torrent["tipo_post"] in ("Série", "Anime"):
        return banco.ano_serie(torrent["titulo"]) or torrent["ano"]
    return torrent["ano"]


def avisar_jellyfin(usuario, texto):
    """Pede ao plugin do Jellyfin que mostre um aviso ao usuário (nas sessões abertas ou na próxima visita)."""
    corpo = json.dumps({"usuario": usuario, "texto": texto}).encode("utf-8")
    pedido = urllib.request.Request(JELLYFIN_URL.rstrip("/") + "/Catalogo/Aviso", data=corpo, method="POST",
                                    headers={"Content-Type": "application/json", "X-Token": CATALOGO_TOKEN})
    with urllib.request.urlopen(pedido, timeout=15) as resposta:
        return resposta.status == 200


def avisos_progresso_jellyfin():
    """Avisa quem pediu pelo Jellyfin quando o download chega a 50% e a 100%."""
    qbt = conectar_qbit()
    if not qbt or not CATALOGO_TOKEN:
        return
    for t in qbt.torrents_info(tag=TAG_JELLYFIN):
        tags = [x.strip() for x in t.tags.split(",")]
        usuario = next((x[3:] for x in tags if x.startswith("jf-") and x not in (TAG_AVISO_50, TAG_AVISO_100)), None)
        if not usuario:
            continue
        nome = os.path.basename(t.save_path.rstrip("/"))
        if t.progress >= 1 and TAG_AVISO_100 not in tags:
            if avisar_jellyfin(usuario, f"Download concluído: {nome}. Já está na biblioteca."):
                qbt.torrents_add_tags(tags=f"{TAG_AVISO_50},{TAG_AVISO_100}", torrent_hashes=t.hash)
        elif 0.5 <= t.progress < 1 and TAG_AVISO_50 not in tags:
            if avisar_jellyfin(usuario, f"Download em 50%: {nome}."):
                qbt.torrents_add_tags(tags=TAG_AVISO_50, torrent_hashes=t.hash)


def renomear_pendentes():
    """Renomeia (no padrão do Jellyfin) os torrents do bot que já receberam a lista de arquivos."""
    qbt = conectar_qbit()
    if not qbt:
        return
    for t in qbt.torrents_info(tag=TAG_RENOMEAR):
        arquivos = qbt.torrents_files(torrent_hash=t.hash)
        if not arquivos:
            continue  # metadados do magnet ainda não chegaram
        info = banco.torrent_por_hash(t.hash)
        if info is None or info["tipo_post"] not in TIPOS_MIDIA:
            qbt.torrents_remove_tags(tags=TAG_RENOMEAR, torrent_hashes=t.hash)
            continue
        info = dict(info)
        temporada = info["temporada"] or info["temporada_post"]
        renomear, ignorar = nomes.planejar([(f.index, f.name, f.size) for f in arquivos], info["tipo_post"],
                                           info["titulo"], ano_da_obra(info), temporada, info["episodio"],
                                           info["episodio_fim"])
        if ignorar:
            qbt.torrents_file_priority(torrent_hash=t.hash, file_ids=ignorar, priority=0)
        for antigo, novo in renomear:
            if antigo == novo:
                continue
            try:
                qbt.torrents_rename_file(torrent_hash=t.hash, old_path=antigo, new_path=novo)
            except Conflict409Error:  # mesmo nome já existe na pasta (outra versão do mesmo filme)
                raiz, ext = os.path.splitext(novo)
                versao = info["resolucao"] or t.hash[:6]
                try:
                    qbt.torrents_rename_file(torrent_hash=t.hash, old_path=antigo, new_path=f"{raiz} - {versao}{ext}")
                except Exception as e:
                    log.warning("Não renomeei %s em %s: %s", antigo, t.name, e)
            except Exception as e:
                log.warning("Não renomeei %s em %s: %s", antigo, t.name, e)
        qbt.torrents_remove_tags(tags=TAG_RENOMEAR, torrent_hashes=t.hash)
        log.info("Renomeado no padrão do Jellyfin: %s -> %s", t.name, t.save_path)


def listar_downloads(chat_id):
    qbt = conectar_qbit()
    if not qbt:
        return None
    tag = f"tg-{chat_id}" if BOT_PUBLICO else TAG_BOT
    return sorted(qbt.torrents_info(tag=tag), key=lambda t: t.added_on, reverse=True)[:15]


def concluidos_sem_aviso():
    qbt = conectar_qbit()
    if not qbt:
        return []
    prontos = []
    for t in qbt.torrents_info(tag=TAG_BOT):
        tags = [x.strip() for x in t.tags.split(",")]
        if t.progress >= 1 and TAG_AVISADO not in tags:
            chats = [int(x[3:]) for x in tags if x.startswith("tg-") and x[3:].lstrip("-").isdigit()]
            prontos.append((t.name, chats))
            qbt.torrents_add_tags(tags=TAG_AVISADO, torrent_hashes=t.hash)
    return prontos


# ==========================================
# FORMATAÇÃO
# ==========================================
def formatar_tamanho(n):
    if not n:
        return ""
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def nome_post(p, curto=False):
    texto = p["titulo"]
    if p["temporada"]:
        texto += f" T{p['temporada']}"
    if p["ano"]:
        texto += f" ({p['ano']})"
    if curto:
        icone = ICONES.get(p["tipo"], "")
        nota = f" ⭐{p['imdb']:.1f}" if p["imdb"] else ""
        texto = f"{icone} {texto}{nota}"
        return texto if len(texto) <= 60 else texto[:57] + "..."
    return texto


def nome_episodio(t):
    if t["completa"] and t["episodio"] is None:
        return "Completa"
    if t["episodio"] is None:
        return ""
    ep = f"E{t['episodio']:02d}"
    if t["episodio_fim"] and t["episodio_fim"] != t["episodio"]:
        ep += f"-{t['episodio_fim']:02d}"
    return ep


def nome_versao(t, com_episodio=True):
    partes = [nome_episodio(t)] if com_episodio else []
    som = " ".join(x for x in (t["audio_codec"], t["canais"]) if x)  # ex: "DD+ 5.1"
    partes += [t["audio"], t["resolucao"], t["fonte"], t["codec"], som, formatar_tamanho(t["tamanho_bytes"])]
    texto = " · ".join(p for p in partes if p)
    return texto or (t["rotulo"] or t["nome_release"] or "Download")[:50]


def texto_post(p):
    linhas = [f"<b>{html.escape(nome_post(p))}</b>"]
    if p["titulo_original"]:
        linhas.append(f"<i>{html.escape(p['titulo_original'])}</i>")
    detalhes = [ICONES.get(p["tipo"], "") + " " + p["tipo"]]
    if p["imdb"]:
        detalhes.append(f"⭐ IMDb {p['imdb']:.1f}")
    if p["duracao"]:
        detalhes.append(f"⏱ {html.escape(p['duracao'])}")
    linhas.append(" · ".join(detalhes))
    if p["generos"]:
        linhas.append(html.escape(p["generos"]))
    if p["cam"]:
        linhas.append("⚠️ Só há versões gravadas no cinema (CAM).")
    linhas.append("\nEscolha uma versão:")
    return "\n".join(linhas)


# ==========================================
# TECLADOS
# ==========================================
def teclado_resultados(resultados, pagina):
    inicio = pagina * RESULTADOS_POR_PAGINA
    botoes = [[InlineKeyboardButton(nome_post(p, curto=True), callback_data=f"p:{p['id']}")]
              for p in resultados[inicio:inicio + RESULTADOS_POR_PAGINA]]
    navegacao = []
    if pagina > 0:
        navegacao.append(InlineKeyboardButton("◀️ Anteriores", callback_data=f"r:{pagina - 1}"))
    if inicio + RESULTADOS_POR_PAGINA < len(resultados):
        navegacao.append(InlineKeyboardButton("Próximos ▶️", callback_data=f"r:{pagina + 1}"))
    if navegacao:
        botoes.append(navegacao)
    return InlineKeyboardMarkup(botoes)


def chave_episodio(t):
    if t["episodio"] is None:
        return "c" if t["completa"] else "x"
    return str(t["episodio"])


def teclado_post(post_id, torrents, tipo, pagina=0):
    serie = tipo in ("Série", "Anime")
    if not serie or len(torrents) <= MAX_VERSOES_DIRETAS:
        botoes = [[InlineKeyboardButton("⬇️ " + nome_versao(t), callback_data=f"d:{t['id']}")] for t in torrents]
        return InlineKeyboardMarkup(botoes)

    # Muitas versões: primeiro escolhe o episódio
    grupos = {}
    for t in torrents:
        grupos.setdefault(chave_episodio(t), t)
    chaves = list(grupos)
    inicio = pagina * EPISODIOS_POR_PAGINA
    visiveis = chaves[inicio:inicio + EPISODIOS_POR_PAGINA]
    botoes, linha = [], []
    for chave in visiveis:
        rotulo = {"c": "📦 Completa", "x": "Outros"}.get(chave) or nome_episodio(grupos[chave])
        linha.append(InlineKeyboardButton(rotulo, callback_data=f"e:{post_id}:{chave}"))
        if len(linha) == 5:
            botoes.append(linha)
            linha = []
    if linha:
        botoes.append(linha)
    navegacao = []
    if pagina > 0:
        navegacao.append(InlineKeyboardButton("◀️", callback_data=f"g:{post_id}:{pagina - 1}"))
    if inicio + EPISODIOS_POR_PAGINA < len(chaves):
        navegacao.append(InlineKeyboardButton("▶️", callback_data=f"g:{post_id}:{pagina + 1}"))
    if navegacao:
        botoes.append(navegacao)
    return InlineKeyboardMarkup(botoes)


def teclado_episodio(post_id, torrents, chave):
    escolhidos = [t for t in torrents if chave_episodio(t) == chave]
    botoes = [[InlineKeyboardButton("⬇️ " + nome_versao(t), callback_data=f"d:{t['id']}")] for t in escolhidos]
    botoes.append([InlineKeyboardButton("⬅️ Voltar aos episódios", callback_data=f"g:{post_id}:0")])
    return InlineKeyboardMarkup(botoes)


# ==========================================
# HANDLERS
# ==========================================
async def autorizado(update: Update) -> bool:
    usuario = update.effective_user
    if usuario and (BOT_PUBLICO or usuario.id in USUARIOS_AUTORIZADOS):
        return True
    if usuario is None:
        return False
    log.warning("Acesso negado para %s (id %s)", usuario.full_name, usuario.id)
    aviso = (f"⛔ Acesso não autorizado.\nSeu ID do Telegram é <code>{usuario.id}</code>.\n"
             "Peça ao administrador para incluí-lo em ALLOWED_USER_IDS.")
    if update.callback_query:
        await update.callback_query.answer("Acesso não autorizado.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(aviso, parse_mode=ParseMode.HTML)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await autorizado(update):
        return
    await update.message.reply_text(
        f"Olá, {html.escape(update.effective_user.first_name)}! 👋\n\n"
        "<b>Buscar:</b> <code>/b nome</code>\n"
        "Você pode adicionar filtros à busca:\n"
        "• qualidade: <code>4k</code>, <code>1080p</code>, <code>720p</code>\n"
        "• áudio: <code>dublado</code>, <code>dual</code>, <code>legendado</code>, <code>nacional</code>\n"
        "• temporada/episódio: <code>s02</code>, <code>s02e05</code>\n"
        "• tipo: <code>filme</code>, <code>serie</code>, <code>anime</code>\n"
        "• <code>cam</code> para incluir gravações de cinema\n\n"
        "Ex: <code>/b duna 1080p dublado</code> · <code>/b the boys s04</code>\n\n"
        "<b>Downloads:</b> <code>/status</code>\n"
        "Aviso aqui quando um download terminar. 🔔",
        parse_mode=ParseMode.HTML)


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await autorizado(update):
        return
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("Uso: <code>/b nome do filme ou série</code>", parse_mode=ParseMode.HTML)
        return
    try:
        resultados, filtros = await asyncio.to_thread(banco.buscar, texto)
    except Exception as e:
        log.exception("Erro na busca")
        await update.message.reply_text(f"Erro ao consultar o banco de dados: {html.escape(str(e))}")
        return

    context.user_data["busca"] = {"ids": [dict(r) for r in resultados], "filtros": filtros, "texto": texto}
    if not resultados:
        await update.message.reply_text(f"Nada encontrado para <b>{html.escape(texto)}</b>.",
                                        parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        f"🔎 <b>{len(resultados)}</b> resultado(s) para <b>{html.escape(texto)}</b>:",
        parse_mode=ParseMode.HTML, reply_markup=teclado_resultados(resultados, 0))


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    if not await autorizado(update):
        return
    acao, _, resto = consulta.data.partition(":")
    filtros = context.user_data.get("busca", {}).get("filtros", {})

    if acao == "r":  # página de resultados
        busca = context.user_data.get("busca")
        if not busca:
            await consulta.answer("Busca expirada. Faça a busca de novo.", show_alert=True)
            return
        await consulta.answer()
        await consulta.edit_message_reply_markup(teclado_resultados(busca["ids"], int(resto)))

    elif acao == "p":  # abrir obra
        await consulta.answer()
        post_id = int(resto)
        post = await asyncio.to_thread(banco.post, post_id)
        torrents = await asyncio.to_thread(banco.torrents_do_post, post_id, filtros)
        if not post or not torrents:
            await consulta.message.reply_text("Esse item não está mais no banco de dados.")
            return
        teclado = teclado_post(post_id, torrents, post["tipo"])
        legenda = texto_post(post)
        if post["capa"]:
            try:
                await consulta.message.reply_photo(post["capa"], caption=legenda, parse_mode=ParseMode.HTML,
                                                   reply_markup=teclado)
                return
            except Exception as e:  # capa fora do ar: manda só o texto
                log.info("Falha ao enviar capa: %s", e)
        await consulta.message.reply_text(legenda, parse_mode=ParseMode.HTML, reply_markup=teclado)

    elif acao in ("g", "e"):  # grade de episódios / versões de um episódio
        await consulta.answer()
        post_id, _, valor = resto.partition(":")
        post = await asyncio.to_thread(banco.post, int(post_id))
        torrents = await asyncio.to_thread(banco.torrents_do_post, int(post_id), filtros)
        if acao == "g":
            teclado = teclado_post(int(post_id), torrents, post["tipo"], int(valor))
        else:
            teclado = teclado_episodio(int(post_id), torrents, valor)
        await consulta.edit_message_reply_markup(teclado)

    elif acao == "d":  # baixar
        torrent = await asyncio.to_thread(banco.torrent, int(resto))
        if not torrent:
            await consulta.answer("Esse torrent não está mais no banco de dados.", show_alert=True)
            return
        await consulta.answer("Enviando ao qBittorrent...")
        resultado = await asyncio.to_thread(adicionar_torrent, dict(torrent), consulta.message.chat_id)
        nome = html.escape(nome_post(torrent))
        versao = html.escape(nome_versao(torrent))
        if resultado == "ok":
            texto = f"✅ Download iniciado!\n\n<b>{nome}</b>\n{versao}\n\nVou avisar quando terminar. 🔔"
        elif resultado == "ja_existe":
            texto = f"ℹ️ Esse torrent já está no qBittorrent.\n\n<b>{nome}</b>\n{versao}"
        elif resultado == "erro_conexao":
            texto = "❌ Não consegui conectar ao qBittorrent. Verifique se ele está ligado."
        else:
            texto = f"❌ {html.escape(resultado)}"
        await consulta.message.reply_text(texto, parse_mode=ParseMode.HTML)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await autorizado(update):
        return
    downloads = await asyncio.to_thread(listar_downloads, update.effective_chat.id)
    if downloads is None:
        await update.message.reply_text("❌ Não consegui conectar ao qBittorrent.")
        return
    if not downloads:
        await update.message.reply_text("Nenhum download enviado pelo bot.")
        return
    titulo = "Seus downloads" if BOT_PUBLICO else "Downloads enviados pelo bot"
    linhas = [f"📥 <b>{titulo}</b>\n"]
    for t in downloads:
        progresso = int(t.progress * 100)
        if progresso >= 100:
            situacao = "✅ concluído"
        else:
            eta = f" · {t.eta // 3600}h{(t.eta % 3600) // 60:02d}m" if 0 < t.eta < 8640000 else ""
            situacao = f"⏳ {progresso}% · {t.dlspeed / 1024 ** 2:.1f} MB/s{eta}"
        linhas.append(f"<b>{html.escape(t.name[:60])}</b>\n{situacao}\n")
    await update.message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)


async def verificar_concluidos(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.to_thread(renomear_pendentes)
    except Exception as e:
        log.warning("Falha ao renomear downloads: %s", e)
    try:
        await asyncio.to_thread(avisos_progresso_jellyfin)
    except Exception as e:
        log.warning("Falha ao avisar progresso no Jellyfin: %s", e)
    try:
        prontos = await asyncio.to_thread(concluidos_sem_aviso)
    except Exception as e:
        log.warning("Falha ao verificar downloads concluídos: %s", e)
        return
    for nome, chats in prontos:
        for chat_id in chats:
            try:
                await context.bot.send_message(chat_id, f"🎉 Download concluído:\n<b>{html.escape(nome)}</b>",
                                               parse_mode=ParseMode.HTML)
            except Exception as e:
                log.warning("Não consegui avisar o chat %s: %s", chat_id, e)


# ==========================================
# API DO CATÁLOGO DO JELLYFIN
# ==========================================
class ApiCatalogo(BaseHTTPRequestHandler):
    """GET /versoes?post=ID lista as versões; POST /baixar?torrent=ID manda ao qBittorrent."""

    def _responder(self, codigo, dados):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _autorizado(self):
        if CATALOGO_TOKEN and hmac.compare_digest(self.headers.get("X-Token", ""), CATALOGO_TOKEN):
            return True
        self._responder(403, {"mensagem": "Chave inválida."})
        return False

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/versoes":
            return self._responder(404, {"mensagem": "?"})
        if not self._autorizado():
            return None
        consulta = parse_qs(url.query)
        try:
            posts = [int(x) for x in (consulta.get("posts") or consulta.get("post"))[0].split(",") if x]
        except (TypeError, ValueError):
            return self._responder(400, {"mensagem": "post inválido"})
        versoes = []
        for post_id in posts:
            for t in banco.torrents_do_post(post_id):
                detalhes = " · ".join(x for x in [t["rotulo"] or "", t["nome_release"] or ""] if x)
                versoes.append({"id": t["id"], "nome": nome_versao(t), "detalhes": detalhes[:300],
                                "temporada": t["temporada"], "episodio": t["episodio"],
                                "episodio_fim": t["episodio_fim"], "completa": bool(t["completa"])})
        self._responder(200, versoes)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/baixar":
            return self._responder(404, {"mensagem": "?"})
        if not self._autorizado():
            return None
        try:
            torrent = banco.torrent(int(parse_qs(url.query)["torrent"][0]))
        except (KeyError, ValueError):
            torrent = None
        if not torrent:
            return self._responder(404, {"mensagem": "Essa versão não está mais no banco."})
        usuario = (parse_qs(url.query).get("usuario") or [""])[0]
        resultado = adicionar_torrent(dict(torrent), None, usuario)
        nome = f"{nome_post(torrent)} - {nome_versao(torrent)}"
        mensagens = {"ok": f"Download iniciado: {nome}. Aparece na biblioteca quando terminar.",
                     "ja_existe": f"Já está no qBittorrent: {nome}.",
                     "ja_baixando": "Outra versão desse filme já está baixando ou já foi baixada.",
                     "erro_conexao": "Não consegui conectar ao qBittorrent."}
        log.info("Catálogo do Jellyfin: %s pediu %s: %s", usuario, nome, resultado)
        self._responder(200, {"mensagem": mensagens.get(resultado, resultado), "ok": resultado == "ok"})

    def log_message(self, formato, *args):
        pass  # sem log por requisição


def iniciar_api_catalogo():
    if not CATALOGO_TOKEN:
        log.warning("CATALOGO_TOKEN vazio: API do Catálogo do Jellyfin desligada.")
        return
    servidor = ThreadingHTTPServer(("0.0.0.0", CATALOGO_PORTA), ApiCatalogo)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    log.info("API do Catálogo do Jellyfin na porta %s.", CATALOGO_PORTA)


async def atualizar_catalogo(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Regera a biblioteca Catálogo do Jellyfin com o que a coleta da noite trouxe."""
    try:
        import catalogo
        await asyncio.to_thread(catalogo.gerar)
    except Exception as e:
        log.warning("Falha ao atualizar o Catálogo do Jellyfin: %s", e)


async def erro(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erro não tratado", exc_info=context.error)


# ==========================================
# INICIALIZAÇÃO
# ==========================================
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise SystemExit("ERRO: TELEGRAM_TOKEN não definido (veja .env.example).")
    if BOT_PUBLICO:
        log.warning("BOT_PUBLICO=1: qualquer pessoa pode buscar e enviar downloads ao qBittorrent.")
    elif not USUARIOS_AUTORIZADOS:
        log.warning("ALLOWED_USER_IDS está vazio: ninguém poderá usar o bot. "
                    "Mande /start para o bot e ele responderá com o seu ID.")
    if not os.path.exists(DB_PATH):
        log.warning("Banco de dados não encontrado em %s", DB_PATH)
    if not conectar_qbit():
        log.warning("qBittorrent indisponível agora; os downloads vão falhar até ele voltar.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler(["b", "buscar"], buscar))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(botoes))
    app.add_error_handler(erro)
    app.job_queue.run_repeating(verificar_concluidos, interval=INTERVALO_AVISOS, first=30)
    iniciar_api_catalogo()
    if os.path.isdir(os.environ.get("PASTA_CATALOGO", "/catalogo")):
        h, m = (int(x) for x in HORA_CATALOGO.split(":"))
        app.job_queue.run_daily(atualizar_catalogo, time=dtime(h, m, tzinfo=datetime.now().astimezone().tzinfo))

    log.info("Bot rodando.")
    app.run_polling()


if __name__ == "__main__":
    main()
