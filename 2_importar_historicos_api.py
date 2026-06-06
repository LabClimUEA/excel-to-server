import argparse
import csv
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


PASTA_BRUTOS = Path("excel-stations")
PASTA_HISTORICOS = Path("dados") / "historicos"
SNAPSHOT_HORA = "07:00:00"
DEFAULT_USER_AGENT = "curl/8.0.0"


def carregar_env(caminho=Path(".env")):
    if not caminho.exists():
        return

    with caminho.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue

            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if chave:
                os.environ.setdefault(chave, valor)


def normalizar_nome(valor):
    texto = unicodedata.normalize("NFKD", valor)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return " ".join(texto.replace("_", " ").replace("-", " ").lower().split())


def nome_estacao_do_historico(caminho):
    return caminho.stem.replace("historico_", "", 1)


def nome_estacao_do_bruto(caminho):
    nome = caminho.stem
    if "-" in nome:
        return nome.split("-", 1)[1].strip().title()
    if "_" in nome:
        return nome.split("_", 1)[1].strip().title()
    return nome.title()


def codigo_estacao_do_bruto(caminho):
    nome = caminho.stem
    codigo = []
    for char in nome:
        if char.isdigit():
            codigo.append(char)
        else:
            break
    return "".join(codigo) or None


def montar_mapa_estacoes():
    mapa = {}
    if not PASTA_BRUTOS.exists():
        return mapa

    for caminho in sorted(PASTA_BRUTOS.glob("*.csv")):
        codigo = codigo_estacao_do_bruto(caminho)
        if not codigo:
            continue

        nome = nome_estacao_do_bruto(caminho)
        mapa[normalizar_nome(nome)] = codigo
        mapa[normalizar_nome(caminho.stem)] = codigo

    return mapa


def resolver_codigo_estacao(nome_estacao, mapa_estacoes):
    if nome_estacao[:8].isdigit():
        return nome_estacao[:8]

    chave = normalizar_nome(nome_estacao)
    if chave in mapa_estacoes:
        return mapa_estacoes[chave]

    for nome_normalizado, codigo in mapa_estacoes.items():
        if chave in nome_normalizado or nome_normalizado in chave:
            return codigo

    return None


def decimal_para_centimetros(valor):
    try:
        numero = Decimal(str(valor).strip().replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None

    return int(numero.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ler_codigo_estacao_do_historico(caminho):
    with caminho.open(newline="", encoding="utf-8") as arquivo:
        primeira_linha = arquivo.readline().strip()

    partes = primeira_linha.split(";")
    if len(partes) == 2 and partes[0] == "code_station" and partes[1]:
        return partes[1]

    return None


def abrir_dados_historico(caminho):
    arquivo = caminho.open(newline="", encoding="utf-8")
    primeira_linha = arquivo.readline()
    if not primeira_linha.startswith("code_station;"):
        arquivo.seek(0)
    return arquivo


def ler_items(caminho):
    items = []
    with abrir_dados_historico(caminho) as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")
        for linha in leitor:
            data = (linha.get("data") or "").strip()
            nivel_cm = linha.get("nivel_cm")
            if not data or not nivel_cm:
                continue

            elevation = decimal_para_centimetros(nivel_cm)
            if elevation is None or elevation == 0:
                continue

            items.append(
                {
                    "date": f"{data}T{SNAPSHOT_HORA}",
                    "elevation": elevation,
                }
            )

    return items


def dividir_em_lotes(items, tamanho_lote):
    if tamanho_lote <= 0:
        yield items
        return

    for inicio in range(0, len(items), tamanho_lote):
        yield items[inicio : inicio + tamanho_lote]


def postar_lote(base_url, token, codigo_estacao, items, timeout, user_agent):
    url = f"{base_url.rstrip('/')}/hydrological-data/station/{codigo_estacao}/historic"
    corpo = json.dumps({"items": items}).encode("utf-8")
    requisicao = urllib.request.Request(
        url,
        data=corpo,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )

    with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
        conteudo = resposta.read().decode("utf-8")
        return json.loads(conteudo) if conteudo else {}


def importar_estacao(caminho, codigo_estacao, args):
    nome_estacao = nome_estacao_do_historico(caminho)
    items = ler_items(caminho)

    if not items:
        print(f"AVISO {nome_estacao}: sem registros validos.")
        return 0, 0

    print(
        f"-> {nome_estacao} ({codigo_estacao}): "
        f"{len(items)} registros em "
        f"{'lote unico' if args.batch_size <= 0 else f'lotes de {args.batch_size}'}"
    )

    if args.dry_run:
        primeiro = items[0]
        ultimo = items[-1]
        print(f"   dry-run: primeiro={primeiro} ultimo={ultimo}")
        return 0, 0

    total_created = 0
    total_skipped = 0

    for indice, lote in enumerate(dividir_em_lotes(items, args.batch_size), start=1):
        for tentativa in range(1, args.retries + 2):
            try:
                resposta = postar_lote(
                    args.base_url,
                    args.token,
                    codigo_estacao,
                    lote,
                    args.timeout,
                    args.user_agent,
                )
                created = int(resposta.get("created", 0))
                skipped = int(resposta.get("skipped", 0))
                total_created += created
                total_skipped += skipped
                print(f"   lote {indice}: created={created} skipped={skipped}")
                if args.batch_delay > 0:
                    time.sleep(args.batch_delay)
                break
            except urllib.error.HTTPError as erro:
                detalhe = erro.read().decode("utf-8", errors="replace")
                if erro.code == 429 and tentativa <= args.retries:
                    retry_after = erro.headers.get("Retry-After")
                    espera = float(retry_after) if retry_after else args.retry_delay
                    print(f"   lote {indice}: HTTP 429, aguardando {espera}s")
                    time.sleep(espera)
                    continue

                raise RuntimeError(
                    f"HTTP {erro.code} ao importar {nome_estacao}: {detalhe}"
                ) from erro
            except (urllib.error.URLError, TimeoutError) as erro:
                if tentativa > args.retries:
                    raise RuntimeError(
                        f"falha de rede ao importar {nome_estacao}: {erro}"
                    ) from erro
                time.sleep(args.retry_delay)

    print(f"   total {nome_estacao}: created={total_created} skipped={total_skipped}")
    return total_created, total_skipped


def parse_args():
    carregar_env()

    parser = argparse.ArgumentParser(
        description="Importa dados historicos por estacao via API REST."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("HYDRO_API_BASE_URL"),
        help="Base da API, ex.: https://dominio.com/api (ou env HYDRO_API_BASE_URL).",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("HYDRO_API_TOKEN"),
        help="Token lab_... (ou env HYDRO_API_TOKEN).",
    )
    parser.add_argument(
        "--historicos-dir",
        default=str(PASTA_HISTORICOS),
        help="Pasta com historico_*.csv.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Quantidade de itens por requisicao. Use 0 para enviar tudo de uma vez.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=30.0)
    parser.add_argument("--batch-delay", type=float, default=2.0)
    parser.add_argument(
        "--user-agent",
        default=os.getenv("HYDRO_API_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent enviado para a API (ou env HYDRO_API_USER_AGENT).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida arquivos e mapeamentos sem chamar a API.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.dry_run and (not args.base_url or not args.token):
        print(
            "ERRO Informe --base-url e --token, ou defina "
            "HYDRO_API_BASE_URL e HYDRO_API_TOKEN.",
            file=sys.stderr,
        )
        return 2

    pasta_historicos = Path(args.historicos_dir)
    if not pasta_historicos.exists():
        print(f"ERRO Pasta nao encontrada: {pasta_historicos}", file=sys.stderr)
        return 2

    arquivos = sorted(pasta_historicos.glob("historico_*.csv"))
    if not arquivos:
        print(f"AVISO Nenhum historico_*.csv encontrado em {pasta_historicos}.")
        return 0

    mapa_estacoes = montar_mapa_estacoes()
    pendentes = []
    estacoes_nao_cadastradas = []
    total_created = 0
    total_skipped = 0

    for caminho in arquivos:
        nome_estacao = nome_estacao_do_historico(caminho)
        codigo_estacao = ler_codigo_estacao_do_historico(caminho)
        if not codigo_estacao:
            codigo_estacao = resolver_codigo_estacao(nome_estacao, mapa_estacoes)
        if not codigo_estacao:
            pendentes.append(nome_estacao)
            continue

        try:
            created, skipped = importar_estacao(caminho, codigo_estacao, args)
            total_created += created
            total_skipped += skipped
        except RuntimeError as erro:
            mensagem_erro = str(erro)
            if mensagem_erro.startswith("HTTP 404"):
                print(
                    f"AVISO {nome_estacao} ({codigo_estacao}): estacao nao cadastrada na API; pulando."
                )
                estacoes_nao_cadastradas.append(f"{nome_estacao} ({codigo_estacao})")
                continue

            print(f"ERRO {erro}", file=sys.stderr)
            return 1

    if estacoes_nao_cadastradas:
        print(
            "AVISO Estacoes nao cadastradas na API: "
            + ", ".join(estacoes_nao_cadastradas)
        )

    if pendentes:
        print(
            "ERRO Nao consegui resolver o codigo destas estacoes: "
            + ", ".join(pendentes),
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("OK Dry-run concluido sem chamar a API.")
    else:
        print(
            f"OK Importacao concluida: created={total_created} skipped={total_skipped}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
