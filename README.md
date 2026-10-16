# torrent-bot

> **EN:** Scraper for WordPress-based torrent sites + Telegram bot that searches the collected database and sends
> the chosen torrent to qBittorrent, renaming everything to Jellyfin's naming standard. It also generates
> "catalog" libraries for Jellyfin and serves the API used by
> [jellyfin-plugin-catalogo](https://github.com/ktrizolio/jellyfin-plugin-catalogo) (download button inside Jellyfin).

Três peças que trabalham juntas num servidor caseiro (Docker):

1. **Scraper:** coleta os posts de um site de torrents feito em **WordPress** (pela API `wp-json`, com o sitemap
   como reserva) e grava um banco **SQLite** limpo: título, ano, tipo, gêneros, nota IMDb, capa e, para cada
   torrent, resolução, áudio (dublado, legendado, dual), temporada, episódio e magnet.
2. **Bot do Telegram:** pesquisa nesse banco e envia a versão escolhida ao **qBittorrent**. Quando o download
   termina, avisa no Telegram.
3. **Catálogo do Jellyfin:** gera bibliotecas com todos os títulos do banco (capa e metadados, vídeo vazio) e
   oferece a API usada pelo plugin [Catálogo (baixar)](https://github.com/ktrizolio/jellyfin-plugin-catalogo).
   Com ela, os usuários do Jellyfin pedem downloads pela própria interface.

Tudo o que é baixado já chega com o **nome no padrão do Jellyfin**: a biblioteca fica organizada sem nenhum
programa extra.

```
Filmes:  /Media/Filmes/Duna (2021)/Duna (2021).mkv
Séries:  /Media/Series/The Boys (2019)/Season 02/The Boys S02E05.mkv
```

> ⚠️ Use apenas com conteúdo que você tem direito de baixar. A ferramenta não hospeda nem indica sites; você
> configura o endereço em `SITE_URL`.

## Funcionalidades

**Scraper** ([scraper/](scraper))
- Coleta incremental: só busca posts novos ou alterados desde a última vez. Uma coleta completa leva poucos
  minutos.
- Extrai os dados de cada torrent na ordem: rótulo ao lado do link, seção da página ("DUAL ÁUDIO"…), nome do
  release no magnet e categorias do post.
- Reclassifica posts marcados como filme que na verdade são jogos, músicas ou programas
  ([classificacao.py](scraper/classificacao.py)).
- Falhas ficam registradas e são tentadas de novo na próxima coleta.
- Opcional: resolve o domínio do site por DNS-over-HTTPS (Cloudflare) em vez da DNS do provedor.
- Roda uma vez, a cada N horas ou todo dia num horário.

**Bot do Telegram** ([bot/bot.py](bot/bot.py))
- `/b nome [filtros]`: busca com filtros `4k` `1080p` `720p`, `dublado` `dual` `legendado` `nacional`, `s02`
  `s02e05`, `filme` `serie` `anime`, `cam`. Os resultados aparecem em páginas com botões. Séries com muitas versões
  mostram uma grade de episódios.
- `/status`: downloads enviados pelo bot.
- Aviso no Telegram quando o download termina.
- Acesso restrito aos IDs autorizados (ou aberto, com `BOT_PUBLICO=1`).

**Downloads organizados** ([bot/nomes.py](bot/nomes.py))
- Cada filme vai para a própria pasta e cada série para `Título (Ano)/Season XX/`. Nunca fica arquivo solto.
- Renomeia vídeos e legendas no padrão do Jellyfin e remove prefixos de site (`[site]`, `www.site.com -`…).
- Arquivos inúteis do torrent (amostras, `.txt`, `.url`, imagens de propaganda) não são baixados.
- Se outra versão do mesmo filme já está no qBittorrent, não baixa de novo.

**Catálogo do Jellyfin** ([bot/catalogo.py](bot/catalogo.py))
- Gera `Catalogo/Filmes` e `Catalogo/Series` com `movie.nfo`/`tvshow.nfo`, `poster.jpg`, `catalogo.json` e um
  vídeo vazio por item (hardlink, não ocupa espaço). Roda todo dia, depois da coleta.
- API HTTP (porta `CATALOGO_PORTA`, chave `CATALOGO_TOKEN`): `GET /versoes` e `POST /baixar`. O contrato está
  no README do plugin.
- Avisa no Jellyfin quem fez o pedido quando o download chega a 50% e a 100%.

## Instalação (Docker)

Pré-requisitos: Docker com Compose, um **qBittorrent** com a Web UI ligada e um bot criado no
[@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/ktrizolio/torrent-bot.git
cd torrent-bot
cp .env.example .env
nano .env            # SITE_URL, TELEGRAM_TOKEN, ALLOWED_USER_IDS, QBIT_*, PASTA_FILMES/SERIES…
docker compose up -d --build
```

Sobem dois containers a partir da mesma imagem:

| Container | O que faz |
|---|---|
| `torrent-scraper` | Atualiza `dados/torrents.db` no horário de `HORARIO_DIARIO` (ou a cada `INTERVALO_HORAS`) |
| `telegram-bot` | Bot, API do Catálogo e geração diária do Catálogo |

Primeiros passos:
- Mande `/start` ao bot: ele responde com o seu ID do Telegram. Coloque-o em `ALLOWED_USER_IDS` e rode
  `docker compose up -d` de novo.
- A primeira coleta leva alguns minutos. Acompanhe com `docker logs -f torrent-scraper`.

### Variáveis principais (`.env`)

| Variável | Para quê |
|---|---|
| `SITE_URL` | Endereço do site WordPress a coletar |
| `HORARIO_DIARIO` / `INTERVALO_HORAS` | Quando o scraper roda |
| `TELEGRAM_TOKEN`, `ALLOWED_USER_IDS`, `BOT_PUBLICO` | Bot e quem pode usá-lo |
| `QBIT_HOST`, `QBIT_PORT`, `QBIT_USERNAME`, `QBIT_PASSWORD` | Acesso à Web UI do qBittorrent |
| `PASTA_FILMES`, `PASTA_SERIES` | Pastas da biblioteca **como o qBittorrent as vê** |
| `CATALOGO_TOKEN`, `CATALOGO_PORTA` | API do Catálogo (vazio = desligada) |
| `JELLYFIN_URL` | Jellyfin, para os avisos de 50%/100% |
| `PASTA_CATALOGO_HOST`, `HORA_CATALOGO` | Onde e quando o Catálogo é gerado |

O `.env` tem senhas e tokens: **nunca** o publique. O `.gitignore` já o exclui.

### Recomendações para o qBittorrent

- Ligue a **pasta de downloads incompletos** (fora da biblioteca), para o Jellyfin só ver arquivos completos.
- Layout de conteúdo: **"Criar subpasta"** para o que for adicionado fora do bot.

## Catálogo do Jellyfin

1. Defina `CATALOGO_TOKEN` (`openssl rand -hex 32`) e `PASTA_CATALOGO_HOST` numa pasta que o Jellyfin enxerga.
2. Crie o vídeo vazio usado por todos os itens (3 s, HEVC, sem áudio):
   ```bash
   mkdir -p "$PASTA_CATALOGO_HOST/.stub"
   ffmpeg -f lavfi -i color=black:s=320x180:d=3 -c:v libx265 "$PASTA_CATALOGO_HOST/.stub/stub.mkv"
   ```
3. Gere o catálogo (depois disso, ele se atualiza sozinho todo dia em `HORA_CATALOGO`):
   ```bash
   docker exec telegram-bot python catalogo.py              # tudo, com capas
   docker exec telegram-bot python catalogo.py --sem-capas  # mais rápido; capas na próxima rodada
   ```
4. Instale o plugin [jellyfin-plugin-catalogo](https://github.com/ktrizolio/jellyfin-plugin-catalogo) e crie as
   bibliotecas *Catálogo Filmes* e *Catálogo Séries* (instruções no README dele).

Se você usa o Tdarr, coloque a pasta do catálogo em *Folders to ignore*.

## Rodar sem Docker

```bash
python -m venv .venv
.venv/bin/pip install -r scraper/requirements.txt -r bot/requirements.txt
.venv/bin/python scraper/scraper.py             # atualização incremental
.venv/bin/python scraper/scraper.py --completo  # recoleta tudo
.venv/bin/python bot/bot.py                     # lê o .env da raiz
```

Testes da padronização de nomes: `cd bot && python -m unittest test_nomes`.

## Estrutura

```
scraper/   scraper.py (coleta), extracao.py (lê cada post), banco.py (esquema SQLite),
           classificacao.py (filme × jogo/música/software), rede.py (HTTP + DNS-over-HTTPS)
bot/       bot.py (Telegram, qBittorrent, API do Catálogo), consultas.py (buscas no banco),
           nomes.py (padrão de nomes do Jellyfin), catalogo.py (gera o Catálogo)
dados/     torrents.db (criado na primeira coleta)
```

## Licença

[MIT](LICENSE)
