# Excel to Server

Utilitario para transformar arquivos CSV historicos da ANA em arquivos normalizados
por estacao e importar esses historicos para a API hidrologica.

O fluxo principal tem duas etapas:

1. Processar os CSVs brutos da ANA em arquivos `dados/historicos/historico_*.csv`.
2. Enviar os historicos processados para a API.

Tambem existe uma etapa opcional para gerar planilhas Excel de percentis por
estacao, a partir dos CSVs brutos.

> **Atencao:** antes de importar historicos, as estacoes correspondentes precisam estar
> cadastradas no servidor. Se a estacao ainda nao existir na API, o historico
> nao sera importado para ela.

## Requisitos

- Python 3
- `pandas`
- Credenciais da API para a etapa de importacao

Instale as dependencias em um ambiente virtual:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Estrutura esperada

```text
excel-stations/
  12520000-Ipixuna.csv
  14620000-Boa vista.csv

dados/historicos/
  historico_Ipixuna.csv
  historico_Boa Vista.csv
  metadata_estacoes.csv
```

Os arquivos brutos devem ficar em `excel-stations/`. O nome do arquivo deve
comecar com o codigo da estacao, seguido do nome, por exemplo:

```text
12520000-Ipixuna.csv
```

## 1. Processar CSVs da ANA

Coloque os arquivos CSV da ANA na pasta `excel-stations/` e execute:

```bash
.venv/bin/python 1_processar_csv.py
```

O script gera:

- `dados/historicos/historico_<Estacao>.csv`: historico diario normalizado.
- `dados/historicos/metadata_estacoes.csv`: metadados e cotas de referencia
  encontradas no cabecalho dos arquivos brutos.

Cada historico gerado inclui a primeira linha `code_station;<codigo>` e depois
as colunas:

```text
data;nivel_cm;nivel_m
```

## 2. Validar antes de importar

Use o modo `--dry-run` para validar os arquivos e os codigos das estacoes sem
enviar dados para a API:

```bash
.venv/bin/python 2_importar_historicos_api.py --dry-run
```

Esse comando lista quantos registros seriam enviados por estacao e mostra o
primeiro e o ultimo item convertido.

## 3. Gerar percentis por estacao

Para gerar um Excel separado com percentis diarios para cada estacao, execute:

```bash
.venv/bin/python 3_gerar_percentis.py
```

Ao executar, o script pergunta se deve usar `dados/historicos/` ou `excel-stations/`.
Respondendo `s`, ele le os arquivos `dados/historicos/historico_*.csv`; respondendo `n`
ou apenas pressionando Enter, ele le os CSVs brutos em `excel-stations/`.

Os arquivos Excel sao gravados em `dados/percentis/percentis_<Estacao>.xlsx`.

Cada Excel agrupa os registros pelo mesmo dia e mes ao longo dos anos
historicos. Por exemplo, todos os registros de `05/06` entram no mesmo calculo.
As colunas geradas incluem:

```text
estacao
codigo_estacao
mes
dia
dia_mes
qtd_registros
min_cm
p05_cm
p10_cm
p15_cm
media_cm
p85_cm
p90_cm
p95_cm
max_cm
min_m
p05_m
p10_m
p15_m
media_m
p85_m
p90_m
p95_m
max_m
```

Os valores numericos nao sao arredondados antes de serem gravados no Excel.

Durante a leitura dos historicos, o script avisa quando encontrar datas invalidas
ou quando uma estacao gerar menos de 365 dias/mes no arquivo de percentis.

## 4. Validar e importar percentis

Valide os arquivos e os payloads sem chamar a API:

```bash
.venv/bin/python 4_importar_percentis_api.py --dry-run
```

Depois da validacao, envie um payload de 366 dias por estacao:

```bash
.venv/bin/python 4_importar_percentis_api.py
```

O importador le `dados/percentis/percentis_*.xlsx`, envia os percentis em
centimetros para `/hydrological-data/percentile/bulk` e inclui o header
`User-Agent`. Antes do envio, valida as 366 linhas, dias `1..366`, codigo da
estacao e a ordem `p5 <= p10 <= p15 <= p85 <= p90 <= p95`.

Opcoes uteis:

```bash
.venv/bin/python 4_importar_percentis_api.py \
  --percentis-dir dados/percentis \
  --user-agent curl/8.0.0 \
  --timeout 60 \
  --retries 5
```

## 5. Configurar credenciais

Crie um arquivo `.env` local:

```env
HYDRO_API_BASE_URL=https://SEU_DOMINIO/api
HYDRO_API_TOKEN=lab_SEU_TOKEN
```

Tambem e possivel informar esses valores por argumento:

```bash
.venv/bin/python 2_importar_historicos_api.py \
  --base-url https://SEU_DOMINIO/api \
  --token lab_SEU_TOKEN
```

## 6. Importar historicos

Depois de validar os dados e confirmar que as estacoes existem na API, execute:

```bash
.venv/bin/python 2_importar_historicos_api.py
```

Por padrao, o importador:

- Le todos os arquivos `dados/historicos/historico_*.csv`.
- Usa `code_station` da primeira linha para identificar a estacao.
- Converte `nivel_cm` para `elevation` inteiro em centimetros.
- Envia datas no formato `YYYY-MM-DDT07:00:00`.
- Envia lotes de 100 registros.
- Aguarda 2 segundos entre lotes.
- Tenta novamente automaticamente em caso de HTTP 429.
- Ignora estacoes que retornarem HTTP 404 e continua a importacao das demais.

Ao final, o script informa o total de registros criados e ignorados pela API.

## Opcoes uteis do importador

```bash
.venv/bin/python 2_importar_historicos_api.py \
  --historicos-dir dados/historicos \
  --batch-size 100 \
  --batch-delay 10 \
  --retries 5 \
  --retry-delay 60 \
  --timeout 30
```

Principais opcoes:

- `--dry-run`: valida sem chamar a API.
- `--historicos-dir`: define a pasta dos arquivos `historico_*.csv`.
- `--batch-size`: quantidade de registros por requisicao. Use `0` para enviar
  tudo em uma unica requisicao.
- `--batch-delay`: pausa entre lotes, em segundos.
- `--retries`: numero de novas tentativas em falhas temporarias.
- `--retry-delay`: pausa padrao antes de tentar novamente.
- `--timeout`: timeout da requisicao, em segundos.

Se a API limitar muitas requisicoes, reduza o tamanho do lote e aumente as
pausas:

```bash
.venv/bin/python 2_importar_historicos_api.py \
  --batch-size 100 \
  --batch-delay 10 \
  --retry-delay 60
```

## Observacoes

- O processador tenta ler arquivos CSV separados por `;` com encoding
  `latin1`, como nos exports da ANA.
- Linhas sem data, sem nivel ou com nivel zero nao sao importadas.
- Se um historico nao tiver `code_station`, o importador tenta resolver o
  codigo a partir dos arquivos brutos em `excel-stations/`.
- Mensagens de HTTP 404 normalmente indicam que a estacao ainda nao foi
  cadastrada na API.
