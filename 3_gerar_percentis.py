import os
import re
import warnings
from calendar import monthrange

import pandas as pd


warnings.filterwarnings("ignore")

PASTA_BRUTOS = os.path.join("excel-stations")
PASTA_HISTORICOS = os.path.join(".", "dados", "historicos")
PASTA_PERCENTIS = os.path.join(".", "dados", "percentis")


def criar_data_completa(data_base, dia):
    if pd.isna(data_base) or pd.isna(dia):
        return pd.NaT

    try:
        dia = int(dia)
        if dia > monthrange(data_base.year, data_base.month)[1]:
            return pd.NaT
        return data_base.replace(day=dia)
    except Exception:
        return pd.NaT


def limpar_nome_estacao(nome_arquivo):
    nome_base = os.path.splitext(nome_arquivo)[0]

    if "-" in nome_base:
        nome_limpo = nome_base.split("-", 1)[1].strip()
    elif "_" in nome_base:
        nome_limpo = nome_base.split("_", 1)[1].strip()
    else:
        nome_limpo = nome_base

    return nome_limpo.title()


def extrair_codigo_estacao(nome_arquivo):
    codigo = []
    for char in nome_arquivo:
        if char.isdigit():
            codigo.append(char)
        else:
            break
    return "".join(codigo)


def nome_estacao_do_historico(nome_arquivo):
    nome_base = os.path.splitext(nome_arquivo)[0]
    return nome_base.replace("historico_", "", 1)


def ler_codigo_estacao_do_historico(caminho_entrada):
    try:
        with open(caminho_entrada, encoding="utf-8") as arquivo:
            primeira_linha = arquivo.readline().strip()
    except Exception:
        return ""

    partes = primeira_linha.split(";")
    if len(partes) == 2 and partes[0] == "code_station":
        return partes[1]

    return ""


def abrir_dados_historico(caminho_entrada):
    arquivo = open(caminho_entrada, encoding="utf-8", newline="")
    primeira_linha = arquivo.readline()
    if not primeira_linha.startswith("code_station;"):
        arquivo.seek(0)
    return arquivo


def nome_arquivo_seguro(valor):
    valor = re.sub(r'[<>:"/\\|?*]', "_", valor)
    valor = re.sub(r"\s+", " ", valor).strip()
    return valor or "estacao"


def converter_cota_para_numero(serie):
    return pd.to_numeric(
        serie.astype(str).str.strip().str.replace(",", ".", regex=False),
        errors="coerce",
    )


def ler_dados_diarios_brutos(caminho_entrada):
    df = pd.read_csv(caminho_entrada, delimiter=";", encoding="latin1", skiprows=15)
    if "Data" not in df.columns:
        df = pd.read_csv(caminho_entrada, delimiter=";", encoding="latin1", skiprows=14)

    cotas_cols = [f"Cota{i:02d}" for i in range(1, 32)]
    cotas_existentes = [coluna for coluna in cotas_cols if coluna in df.columns]
    if not cotas_existentes:
        return pd.DataFrame(columns=["data", "nivel_cm"])

    dados = df[["Data"] + cotas_existentes].drop_duplicates()
    dados["Data"] = pd.to_datetime(dados["Data"], format="%d/%m/%Y", errors="coerce")

    dados_long = pd.melt(
        dados,
        id_vars=["Data"],
        value_vars=cotas_existentes,
        var_name="ColunaDia",
        value_name="Cota",
    )
    dados_long["Dia"] = dados_long["ColunaDia"].str.extract(r"(\d+)").astype(float)
    dados_long["data"] = dados_long.apply(
        lambda row: criar_data_completa(row["Data"], row["Dia"]), axis=1
    )
    dados_long["nivel_cm"] = converter_cota_para_numero(dados_long["Cota"])

    dados_diarios = dados_long[["data", "nivel_cm"]].dropna()
    dados_diarios = dados_diarios[dados_diarios["nivel_cm"] != 0]
    if dados_diarios.empty:
        return dados_diarios

    return (
        dados_diarios.groupby("data", as_index=False)["nivel_cm"]
        .mean()
        .sort_values(by="data")
    )


def converter_datas_historico(serie):
    textos = serie.astype(str).str.strip()
    datas = pd.to_datetime(textos, format="%Y-%m-%d", errors="coerce")

    formatos = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for formato in formatos:
        pendentes = datas.isna()
        if not pendentes.any():
            break
        datas.loc[pendentes] = pd.to_datetime(
            textos.loc[pendentes], format=formato, errors="coerce"
        )

    return datas


def ler_dados_diarios_historico(caminho_entrada, nome_estacao):
    with abrir_dados_historico(caminho_entrada) as arquivo:
        df = pd.read_csv(arquivo, delimiter=";", decimal=",")

    if "data" not in df.columns:
        return pd.DataFrame(columns=["data", "nivel_cm"])

    datas_convertidas = converter_datas_historico(df["data"])
    datas_invalidas = int(datas_convertidas.isna().sum())
    if datas_invalidas:
        print(f"\n   AVISO {nome_estacao}: {datas_invalidas} datas invalidas foram ignoradas.")

    dados_diarios = pd.DataFrame()
    dados_diarios["data"] = datas_convertidas
    if "nivel_cm" in df.columns:
        dados_diarios["nivel_cm"] = converter_cota_para_numero(df["nivel_cm"])
    elif "nivel_m" in df.columns:
        dados_diarios["nivel_cm"] = converter_cota_para_numero(df["nivel_m"]) * 100
    else:
        return pd.DataFrame(columns=["data", "nivel_cm"])
    dados_diarios = dados_diarios.dropna()
    dados_diarios = dados_diarios[dados_diarios["nivel_cm"] != 0]
    if dados_diarios.empty:
        return dados_diarios

    return (
        dados_diarios.groupby("data", as_index=False)["nivel_cm"]
        .mean()
        .sort_values(by="data")
    )


def percentil(serie, valor):
    return serie.quantile(valor)


def calcular_percentis(dados_diarios, nome_estacao, codigo_estacao):
    dados = dados_diarios.copy()
    dados["mes"] = dados["data"].dt.month
    dados["dia"] = dados["data"].dt.day
    dados["dia_mes"] = dados["data"].dt.strftime("%d/%m")

    estatisticas = (
        dados.groupby(["mes", "dia", "dia_mes"])["nivel_cm"]
        .agg(
            qtd_registros="count",
            min_cm="min",
            p05_cm=lambda serie: percentil(serie, 0.05),
            p10_cm=lambda serie: percentil(serie, 0.10),
            p15_cm=lambda serie: percentil(serie, 0.15),
            media_cm="mean",
            p85_cm=lambda serie: percentil(serie, 0.85),
            p90_cm=lambda serie: percentil(serie, 0.90),
            p95_cm=lambda serie: percentil(serie, 0.95),
            max_cm="max",
        )
        .reset_index()
        .sort_values(["mes", "dia"])
    )

    estatisticas.insert(0, "codigo_estacao", codigo_estacao)
    estatisticas.insert(0, "estacao", nome_estacao)

    colunas_cm = [
        "min_cm",
        "p05_cm",
        "p10_cm",
        "p15_cm",
        "media_cm",
        "p85_cm",
        "p90_cm",
        "p95_cm",
        "max_cm",
    ]
    for coluna in colunas_cm:
        estatisticas[coluna.replace("_cm", "_m")] = estatisticas[coluna] / 100

    return estatisticas[
        [
            "estacao",
            "codigo_estacao",
            "mes",
            "dia",
            "dia_mes",
            "qtd_registros",
            "min_cm",
            "p05_cm",
            "p10_cm",
            "p15_cm",
            "media_cm",
            "p85_cm",
            "p90_cm",
            "p95_cm",
            "max_cm",
            "min_m",
            "p05_m",
            "p10_m",
            "p15_m",
            "media_m",
            "p85_m",
            "p90_m",
            "p95_m",
            "max_m",
        ]
    ]


def processar_arquivo(nome_arquivo, pasta_entrada, usar_historicos):
    caminho_entrada = os.path.join(pasta_entrada, nome_arquivo)
    if usar_historicos:
        nome_estacao = nome_estacao_do_historico(nome_arquivo)
        codigo_estacao = ler_codigo_estacao_do_historico(caminho_entrada)
        if not codigo_estacao:
            print(f"🔄 {nome_arquivo} -> {nome_estacao}... ❌ Sem code_station; pulando.")
            return
        leitor_dados = ler_dados_diarios_historico
    else:
        nome_estacao = limpar_nome_estacao(nome_arquivo)
        codigo_estacao = extrair_codigo_estacao(nome_arquivo)
        leitor_dados = ler_dados_diarios_brutos

    print(f"🔄 {nome_arquivo} -> {nome_estacao}...", end=" ")

    try:
        dados_diarios = leitor_dados(caminho_entrada, nome_estacao) if usar_historicos else leitor_dados(caminho_entrada)
        if dados_diarios.empty:
            print("❌ Sem dados validos.")
            return

        estatisticas = calcular_percentis(
            dados_diarios, nome_estacao=nome_estacao, codigo_estacao=codigo_estacao
        )

        total_dias = len(estatisticas)
        if total_dias < 365:
            print(f"\n   AVISO {nome_estacao}: percentis gerados com apenas {total_dias} dias/mes. Confira o formato das datas ou se o historico esta incompleto.")

        nome_saida = f"percentis_{nome_arquivo_seguro(nome_estacao)}.xlsx"
        caminho_saida = os.path.join(PASTA_PERCENTIS, nome_saida)
        with pd.ExcelWriter(caminho_saida, engine="openpyxl") as writer:
            estatisticas.to_excel(
                writer, sheet_name="percentis_diarios", index=False
            )

        print(f"✅ {caminho_saida}")
    except Exception as erro:
        print(f"❌ Erro: {erro}")


def perguntar_usar_historicos():
    resposta = input("Usar dados de dados/historicos em vez de excel-stations? [s/N]: ")
    return resposta.strip().lower() in ("s", "sim", "y", "yes")


def main():
    print("--- GERADOR DE PERCENTIS DIARIOS POR ESTACAO ---")

    usar_historicos = perguntar_usar_historicos()
    pasta_entrada = PASTA_HISTORICOS if usar_historicos else PASTA_BRUTOS
    padrao_nome = "historico_*.csv" if usar_historicos else "*.csv"

    if not os.path.exists(pasta_entrada):
        print(f"❌ Pasta nao encontrada: {pasta_entrada}")
        return

    os.makedirs(PASTA_PERCENTIS, exist_ok=True)

    arquivos = sorted(
        nome
        for nome in os.listdir(pasta_entrada)
        if nome.lower().endswith(".csv")
        and (not usar_historicos or nome.startswith("historico_"))
    )
    if not arquivos:
        print(f"❌ Nenhum arquivo {padrao_nome} encontrado em {pasta_entrada}.")
        return

    for nome_arquivo in arquivos:
        processar_arquivo(nome_arquivo, pasta_entrada, usar_historicos)


if __name__ == "__main__":
    main()
