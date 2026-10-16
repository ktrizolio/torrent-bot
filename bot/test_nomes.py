"""Testes da renomeação no padrão do Jellyfin. Rodar: python -m unittest bot/test_nomes.py (ou dentro de bot/)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nomes import (codigo_episodio, com_ano, destino, limpar_release, nome_seguro,  # noqa: E402
                   planejar)


def caminho_final(release, arquivo, tipo, ano_conhecido=None):
    """Simula o bot: limpa o release, monta a pasta e renomeia o arquivo."""
    titulo, ano, ep = limpar_release(release)
    ano = ano or ano_conhecido
    pasta = destino(tipo, titulo, ano).split("/", 3)[-1]  # sem /Media/Filmes|Series
    temporada, episodio, fim = ep if ep else (None, None, None)
    renomear, _ = planejar([(0, arquivo, 2 * 1024 ** 3)], tipo, titulo, ano, temporada, episodio, fim)
    return f"{pasta}/{renomear[0][1]}"


class CasosReais(unittest.TestCase):
    def test_coyote_prefixo_site(self):
        self.assertEqual(
            caminho_final("[www.UIndex.org]    -    Coyote.vs.Acme.2026.1080p.DCP.DDP5.1.H264-AOC",
                          "Coyote.vs.Acme.2026.1080p.DCP.DDP5.1.H264-AOC.mkv", "Filme"),
            "Coyote vs. Acme (2026)/Coyote vs. Acme (2026).mkv")

    def test_batalha_pontos(self):
        self.assertEqual(
            caminho_final("Uma.Batalha.Após.a.Outra.2025.1080p.WEB-DL.DUAL.5.1",
                          "Uma.Batalha.Após.a.Outra.2025.1080p.WEB-DL.DUAL.5.1.mkv", "Filme"),
            "Uma Batalha Após a Outra (2025)/Uma Batalha Após a Outra (2025).mkv")

    def test_ainda_estou_aqui(self):
        self.assertEqual(
            caminho_final("Ainda.Estou.Aqui.2024.1080p.WEB-DL.DUAL.5.1",
                          "Ainda.Estou.Aqui.2024.1080p.WEB-DL.DUAL.5.1.mkv", "Filme"),
            "Ainda Estou Aqui (2024)/Ainda Estou Aqui (2024).mkv")

    def test_shoplifters_idioma(self):
        self.assertEqual(
            caminho_final("Shoplifters JAPANESE (2018)", "Shoplifters JAPANESE (2018) 720p AAC.mkv", "Filme"),
            "Shoplifters (2018)/Shoplifters (2018).mkv")

    def test_pluribus_episodio(self):
        self.assertEqual(
            caminho_final("Pluribus S01E06 WEB-DL 1080p x264 DUAL 5.1",
                          "Pluribus S01E06 WEB-DL 1080p x264 DUAL 5.1.mkv", "Série", ano_conhecido=2025),
            "Pluribus (2025)/Season 01/Pluribus S01E06.mkv")


class Limpeza(unittest.TestCase):
    def test_numero_no_titulo(self):
        self.assertEqual(limpar_release("2001.A.Space.Odyssey.1968.1080p.BluRay.x264")[:2],
                         ("2001 A Space Odyssey", 1968))

    def test_ano_entre_parenteses_com_acentos(self):
        self.assertEqual(limpar_release("O Auto da Compadecida (2000) 1080p NACIONAL")[:2],
                         ("O Auto da Compadecida", 2000))

    def test_dois_pontos(self):
        self.assertEqual(nome_seguro("IT: Bem-Vindos à Derry"), "IT - Bem-Vindos à Derry")
        self.assertEqual(com_ano("2001: Uma Odisseia no Espaço", 1968), "2001 - Uma Odisseia no Espaço (1968)")

    def test_caracteres_proibidos(self):
        self.assertEqual(nome_seguro('Quem? Eu "não" <sei> | a/b \\ c*'), "Quem Eu não sei a b c")

    def test_sem_ano(self):
        self.assertEqual(limpar_release("Some.Movie.1080p.WEB-DL.x264-GRP")[:2], ("Some Movie", None))

    def test_codigos_de_episodio(self):
        self.assertEqual(codigo_episodio("Gen.V.S02E01-02-03.1080p"), (2, 1, 3))
        self.assertEqual(codigo_episodio("Show S01E01E02"), (1, 1, 2))
        self.assertEqual(codigo_episodio("Show 3x07 HDTV"), (3, 7, None))
        self.assertIsNone(codigo_episodio("Filme 2024 1080p"))


class Planejamento(unittest.TestCase):
    def test_filme_lixo_legenda_sample(self):
        arquivos = [
            (0, "BAIXAR FILMES.url", 47),
            (1, "Filme.2020.1080p.mkv", 4 * 1024 ** 3),
            (2, "Sample/sample.mkv", 30 * 1024 ** 2),
            (3, "Filme.2020.pt-BR.srt", 90_000),
            (4, "Filme.2020.eng.srt", 80_000),
        ]
        renomear, ignorar = planejar(arquivos, "Filme", "Filme", 2020)
        self.assertEqual(sorted(ignorar), [0, 2])
        self.assertIn(("Filme.2020.1080p.mkv", "Filme (2020).mkv"), renomear)
        self.assertIn(("Filme.2020.pt-BR.srt", "Filme (2020).pt-BR.srt"), renomear)
        self.assertIn(("Filme.2020.eng.srt", "Filme (2020).en.srt"), renomear)

    def test_pacote_de_temporada(self):
        arquivos = [(i, f"Show.S02E0{i}.1080p.WEB-DL.mkv", 1024 ** 3) for i in range(1, 4)]
        arquivos.append((9, "Show.S02E01.pt-BR.srt", 50_000))
        renomear, ignorar = planejar(arquivos, "Série", "The Show", 2024, temporada=2)
        self.assertEqual(ignorar, [])
        self.assertIn(("Show.S02E02.1080p.WEB-DL.mkv", "Season 02/The Show S02E02.mkv"), renomear)
        self.assertIn(("Show.S02E01.pt-BR.srt", "Season 02/The Show S02E01.pt-BR.srt"), renomear)

    def test_episodio_sem_codigo_no_arquivo(self):
        renomear, _ = planejar([(0, "episodio.mkv", 1024 ** 3)], "Série", "Dark Matter", 2024,
                               temporada=2, episodio=7)
        self.assertEqual(renomear, [("episodio.mkv", "Season 02/Dark Matter S02E07.mkv")])

    def test_destinos(self):
        self.assertEqual(destino("Filme", "Coyote vs. ACME", 2026), "/Media/Filmes/Coyote vs. ACME (2026)")
        self.assertEqual(destino("Anime", "Frieren", 2023), "/Media/Series/Frieren (2023)")


if __name__ == "__main__":
    unittest.main()
