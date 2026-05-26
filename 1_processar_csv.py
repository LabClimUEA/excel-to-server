import pandas as pd
import os
import warnings
from calendar import monthrange

warnings.filterwarnings("ignore")

# --- CONFIGURAÇÃO DE PASTAS ---
PASTA_BRUTOS = os.path.join("excel-stations")
PASTA_HISTORICOS = os.path.join(".", "dados", "historicos")

if not os.path.exists(PASTA_HISTORICOS):
    os.makedirs(PASTA_HISTORICOS)


def criar_data_completa(data_base, dia):
    if pd.isna(data_base) or pd.isna(dia):
        return pd.NaT
    try:
        if dia > monthrange(data_base.year, data_base.month)[1]:
            return pd.NaT
        return data_base.replace(day=int(dia))
    except:
        return pd.NaT


def extrair_cotas_referencia(caminho_arquivo):
    """Lê cabeçalho para extrair Alerta, Inundação, etc."""
    limites = {
        "alerta": None,
        "inundacao": None,
        "inundacao_severa": None,
        "estiagem": None,
        "seca_severa": None,
    }
    try:
        with open(caminho_arquivo, "r", encoding="latin1") as f:
            linhas = [f.readline().strip() for _ in range(25)]

        headers, valores = [], []
        for i, linha in enumerate(linhas):
            if "indíces" in linha.lower() or "indices" in linha.lower():
                headers = linha.split(";")[1:]
                if i + 1 < len(linhas) and "cotaind" in linhas[i + 1].lower():
                    valores = linhas[i + 1].split(";")[1:]
                break

        for h, v in zip(headers, valores):
            h = h.strip().lower()
            try:
                v_limpo = v.replace(".", "").replace(",", ".")
                valor_m = float(v_limpo) / 100
            except:
                continue

            if "alerta" in h:
                limites["alerta"] = valor_m
            elif "severa" in h and "inunda" in h:
                limites["inundacao_severa"] = valor_m
            elif "inunda" in h:
                limites["inundacao"] = valor_m
            elif "estiagem" in h:
                limites["estiagem"] = valor_m
            elif "seca" in h:
                limites["seca_severa"] = valor_m
    except:
        pass
    return limites


def limpar_nome_estacao(nome_arquivo):
    """
    Transforma '14990000-MANAUS' em 'Manaus'
    Transforma '15400000-PORTO VELHO' em 'Porto Velho'
    """
    nome_base = os.path.splitext(nome_arquivo)[0]  # Remove .csv

    # Se tiver hífen, pega o que vem depois dele (o nome da cidade)
    if "-" in nome_base:
        partes = nome_base.split("-", 1)  # Divide apenas no primeiro hífen
        nome_limpo = partes[1].strip()
    else:
        nome_limpo = nome_base

    # Converte para Title Case (ex: PORTO VELHO -> Porto Velho)
    return nome_limpo.title()


def extrair_codigo_estacao(nome_arquivo):
    codigo = []
    for char in nome_arquivo:
        if char.isdigit():
            codigo.append(char)
        else:
            break
    return "".join(codigo)


def processar_arquivo(nome_arquivo, lista_metadata):
    caminho_entrada = os.path.join(PASTA_BRUTOS, nome_arquivo)

    # --- NOVO: LIMPEZA DO NOME (Extrai só a cidade) ---
    nome_estacao = limpar_nome_estacao(nome_arquivo)
    codigo_estacao = extrair_codigo_estacao(nome_arquivo)

    # Salva o arquivo histórico com o nome LIMPO para o banco ler certo
    caminho_saida = os.path.join(PASTA_HISTORICOS, f"historico_{nome_estacao}.csv")

    print(f"🔄 {nome_arquivo} -> {nome_estacao}...", end=" ")

    # 1. Metadados
    cotas = extrair_cotas_referencia(caminho_entrada)
    lista_metadata.append(
        {
            "code_station": codigo_estacao,
            "estacao": nome_estacao,  # Salva no metadata como "Manaus"
            "alerta": cotas["alerta"],
            "inundacao": cotas["inundacao"],
            "inundacao_severa": cotas["inundacao_severa"],
            "estiagem": cotas["estiagem"],
            "seca_severa": cotas["seca_severa"],
        }
    )

    # 2. Dados
    try:
        df = pd.read_csv(caminho_entrada, delimiter=";", encoding="latin1", skiprows=15)
        if "Data" not in df.columns:
            df = pd.read_csv(
                caminho_entrada, delimiter=";", encoding="latin1", skiprows=14
            )

        cotas_cols = [f"Cota{i:02d}" for i in range(1, 32)]
        cotas_existentes = [c for c in cotas_cols if c in df.columns]

        if not cotas_existentes:
            print("❌ Sem dados.")
            return

        colunas = ["Data"] + cotas_existentes
        dados = df[colunas].drop_duplicates()
        dados["Data"] = pd.to_datetime(
            dados["Data"], format="%d/%m/%Y", errors="coerce"
        )

        dados_long = pd.melt(
            dados, id_vars=["Data"], value_vars=cotas_existentes, value_name="Cota"
        )
        dados_long["Dia"] = dados_long["variable"].str.extract(r"(\d+)").astype(float)
        dados_long["DataCompleta"] = dados_long.apply(
            lambda row: criar_data_completa(row["Data"], row["Dia"]), axis=1
        )

        dados_diarios = (
            dados_long[["DataCompleta", "Cota"]].dropna().sort_values(by="DataCompleta")
        )

        if dados_diarios["Cota"].dtype == object:
            dados_diarios["Cota"] = (
                dados_diarios["Cota"].astype(str).str.replace(",", ".").astype(float)
            )

        dados_diarios = dados_diarios[dados_diarios["Cota"] != 0]

        if dados_diarios.empty:
            print("❌ Vazio.")
            return

        df_final = (
            dados_diarios.groupby("DataCompleta")["Cota"].mean().round(0).reset_index()
        )
        df_final.rename(
            columns={"DataCompleta": "data", "Cota": "nivel_cm"}, inplace=True
        )
        df_final["nivel_m"] = df_final["nivel_cm"] / 100
        df_final["data"] = df_final["data"].dt.strftime("%Y-%m-%d")

        with open(caminho_saida, "w", encoding="utf-8", newline="") as arquivo_saida:
            arquivo_saida.write(f"code_station;{codigo_estacao}\n")
            df_final.to_csv(arquivo_saida, index=False, sep=";", decimal=",")
        print(f"✅ OK")

    except Exception as e:
        print(f"❌ Erro: {e}")


def main():
    print("--- PROCESSADOR INTELIGENTE (LIMPA NOMES + METADATA) ---")
    if not os.path.exists(PASTA_BRUTOS):
        return
    arquivos = [f for f in os.listdir(PASTA_BRUTOS) if f.lower().endswith(".csv")]

    lista_metadata = []
    for arq in arquivos:
        processar_arquivo(arq, lista_metadata)

    if lista_metadata:
        df_meta = pd.DataFrame(lista_metadata)
        cols = [
            "code_station",
            "estacao",
            "alerta",
            "inundacao",
            "inundacao_severa",
            "estiagem",
            "seca_severa",
        ]
        df_meta = df_meta[cols]
        caminho_meta = os.path.join(PASTA_HISTORICOS, "metadata_estacoes.csv")
        df_meta.to_csv(caminho_meta, index=False, sep=";", decimal=",")
        print(f"\n📋 Metadados gerados.")


if __name__ == "__main__":
    main()

