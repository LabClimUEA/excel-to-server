import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd


PASTA_PERCENTIS = Path("dados") / "percentis"
DEFAULT_USER_AGENT = "curl/8.0.0"
COLUNAS_PERCENTIS = [
    ("p05_cm", "percentile_5"),
    ("p10_cm", "percentile_10"),
    ("p15_cm", "percentile_15"),
    ("p85_cm", "percentile_85"),
    ("p90_cm", "percentile_90"),
    ("p95_cm", "percentile_95"),
]


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


def numero_json(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError) as erro:
        raise ValueError(f"valor nao numerico: {valor}") from erro

    if not math.isfinite(numero):
        raise ValueError(f"valor nao finito: {valor}")
    return numero


def dia_do_ano(mes, dia):
    try:
        return date(2000, int(mes), int(dia)).timetuple().tm_yday
    except (TypeError, ValueError) as erro:
        raise ValueError(f"mes/dia invalido: {mes}/{dia}") from erro


def codigo_estacao_valido(valor):
    if pd.isna(valor):
        return None

    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)

    codigo = str(valor).strip()
    return codigo if codigo.isdigit() else None


def ler_payload(caminho):
    df = pd.read_excel(caminho, sheet_name="percentis_diarios")
    colunas_obrigatorias = {
        "codigo_estacao",
        "mes",
        "dia",
        *(coluna for coluna, _ in COLUNAS_PERCENTIS),
    }
    faltantes = sorted(colunas_obrigatorias - set(df.columns))
    if faltantes:
        raise ValueError(f"colunas ausentes: {', '.join(faltantes)}")

    if len(df) != 366:
        raise ValueError(f"esperadas 366 linhas, encontradas {len(df)}")

    codigos_normalizados = [codigo_estacao_valido(valor) for valor in df["codigo_estacao"]]
    if any(codigo is None for codigo in codigos_normalizados):
        raise ValueError("todas as linhas precisam conter um codigo_estacao valido")

    codigos = set(codigos_normalizados)
    if len(codigos) != 1:
        raise ValueError("o arquivo precisa conter exatamente um codigo_estacao")
    station_id = codigos.pop()

    items = []
    for indice, linha in df.iterrows():
        try:
            day = dia_do_ano(linha["mes"], linha["dia"])
            valores = [numero_json(linha[coluna]) for coluna, _ in COLUNAS_PERCENTIS]
        except ValueError as erro:
            raise ValueError(f"linha {indice + 2}: {erro}") from erro

        if valores != sorted(valores):
            raise ValueError(
                f"linha {indice + 2}: percentis fora de ordem "
                "(p5 <= p10 <= p15 <= p85 <= p90 <= p95)"
            )

        item = {"day": day}
        for (_, campo_api), valor in zip(COLUNAS_PERCENTIS, valores):
            item[campo_api] = valor
        items.append(item)

    dias = [item["day"] for item in items]
    if len(set(dias)) != 366 or set(dias) != set(range(1, 367)):
        raise ValueError("os dias precisam ser unicos e cobrir exatamente 1..366")

    items.sort(key=lambda item: item["day"])
    return {"station_id": station_id, "data": items}


def postar_percentis(base_url, token, payload, timeout, user_agent):
    url = f"{base_url.rstrip('/')}/hydrological-data/percentile/bulk"
    corpo = json.dumps(payload, allow_nan=False).encode("utf-8")
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


def importar_estacao(caminho, payload, args):
    nome_estacao = caminho.stem.replace("percentis_", "", 1)
    station_id = payload["station_id"]

    print(f"-> {nome_estacao} ({station_id}): {len(payload['data'])} dias")
    if args.dry_run:
        print(
            f"   dry-run: primeiro={payload['data'][0]} "
            f"ultimo={payload['data'][-1]}"
        )
        return 0, 0

    for tentativa in range(1, args.retries + 2):
        try:
            resposta = postar_percentis(
                args.base_url,
                args.token,
                payload,
                args.timeout,
                args.user_agent,
            )
            created = int(resposta.get("created", 0))
            skipped = int(resposta.get("skipped", 0))
            print(f"   created={created} skipped={skipped}")
            return created, skipped
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")
            if erro.code == 429 and tentativa <= args.retries:
                retry_after = erro.headers.get("Retry-After")
                espera = float(retry_after) if retry_after else args.retry_delay
                print(f"   HTTP 429, aguardando {espera}s")
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

    return 0, 0


def parse_args():
    carregar_env()

    parser = argparse.ArgumentParser(
        description="Importa percentis hidrologicos por estacao via API REST."
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
        "--percentis-dir",
        default=str(PASTA_PERCENTIS),
        help="Pasta com percentis_*.xlsx.",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=30.0)
    parser.add_argument(
        "--user-agent",
        default=os.getenv("HYDRO_API_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent enviado para a API (ou env HYDRO_API_USER_AGENT).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida arquivos e payloads sem chamar a API.",
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

    pasta_percentis = Path(args.percentis_dir)
    if not pasta_percentis.exists():
        print(f"ERRO Pasta nao encontrada: {pasta_percentis}", file=sys.stderr)
        return 2

    arquivos = sorted(pasta_percentis.glob("percentis_*.xlsx"))
    if not arquivos:
        print(f"AVISO Nenhum percentis_*.xlsx encontrado em {pasta_percentis}.")
        return 0

    falhas = []
    station_ids = set()
    estacoes_validas = 0
    total_created = 0
    total_skipped = 0

    for caminho in arquivos:
        try:
            payload = ler_payload(caminho)
            station_id = payload["station_id"]
            if station_id in station_ids:
                raise ValueError(f"station_id duplicado entre arquivos: {station_id}")
            station_ids.add(station_id)

            created, skipped = importar_estacao(caminho, payload, args)
            estacoes_validas += 1
            total_created += created
            total_skipped += skipped
        except Exception as erro:
            print(f"ERRO {caminho.name}: {erro}", file=sys.stderr)
            falhas.append(f"{caminho.name}: {erro}")

    if args.dry_run:
        print(
            f"OK Dry-run concluido: estacoes_validas={estacoes_validas} "
            f"falhas={len(falhas)}"
        )
    else:
        print(
            f"OK Importacao concluida: created={total_created} "
            f"skipped={total_skipped} falhas={len(falhas)}"
        )

    if falhas:
        print("ERRO Estacoes com falha:", file=sys.stderr)
        for falha in falhas:
            print(f"- {falha}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
