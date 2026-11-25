# BeeGuard — datasets e treinamento de IA

## Dados disponíveis nesta cópia

Os CSVs originais de inspeções UrBAN estão em `data/raw/urban/annotations/`, junto de `provenance.json`, que registra URLs, data de obtenção e hashes. Os derivados estão em `data/metadata/urban_inspections/`. Os áudios públicos, os manifestos completos e modelos treinados não estão incluídos nesta cópia.

Mantenha originais em `data/raw/`, manifestos em `data/metadata/`, versões preparadas em `data/processed/` e modelos em `models/`. Não altere os CSVs originais para corrigir rótulos: preserve a proveniência em novos derivados.

## Exportar dados locais e treinar interferência sonora

Execute na raiz, depois de coletar e revisar dados reais:

```powershell
.\.venv\Scripts\python.exe run.py export_jatai --species jatai_unconfirmed --output data/processed/jatai_v1
.\.venv\Scripts\python.exe run.py train_field --dataset data/processed/jatai_v1/training_features.csv --output models/jatai_sound_v1
```

Use a espécie confirmada quando houver identificação. A exportação inclui manifesto, features elegíveis, sessões, inspeções e relatório. `--copy-audio` cria uma cópia transportável. Escolha uma pasta nova a cada versão.

Classes: `clean_bee_sound`, `external_interference` e `uncertain` (excluída do treino padrão). São excluídos exemplos de bancada, não revisados, incertos, com problemas de qualidade, inconsistência de hash ou features incompatíveis. Duplicatas elegíveis são removidas para reduzir vazamento entre grupos.

O treinador usa 40 features, padronização e regressão logística balanceada. Exige pelo menos 20 gravações, três colmeias e cinco exemplos de cada classe. Avalia deixando uma colmeia fora do treino e compara com a classe majoritária. Esses mínimos técnicos não demonstram suficiência científica. Produz `sound_model.joblib`, `training_report.json` e `oof_predictions.csv`.

## Preparar WAVs e extrair features

```powershell
.\.venv\Scripts\python.exe run.py index_recordings --input data/raw/beeguard --output data/metadata/beeguard.csv --source beeguard --hive-id BG01
.\.venv\Scripts\python.exe run.py prepare_dataset --manifest data/metadata/beeguard.csv --output data/processed/beeguard_v1
.\.venv\Scripts\python.exe run.py extract_features --metadata data/processed/beeguard_v1/metadata.csv --output data/processed/beeguard_features.csv
```

O indexador só deve receber um hive-id único se todos os áudios forem da mesma caixa. Os caminhos do manifesto são relativos à pasta do CSV. O pipeline gera janelas de 10 segundos e verifica corrupção, clipping, amplitude baixa e duplicação. Não aplica normalização de pico nem redução de ruído automática.

## UrBAN e população experimental

Ferramentas disponíveis: `inspect_urban`, `import_urban`, `label_urban_population`, `train_population` e `audit_population`. Consulte as opções com `python run.py NOME --help`. A inspeção dos CSVs incluídos pode ser repetida com:

```powershell
.\.venv\Scripts\python.exe run.py inspect_urban --output data/metadata/urban_inspections_v2
```

As etapas seguintes dependem de áudios, manifestos e features externos. Os rótulos de população podem ser interpolados entre inspeções e devem ser identificados como derivados. O treinador usa Random Forest e validação por colmeia. O histórico do piloto e as referências das bases estão no [manual completo](referencia/README.md).

## Interpretação

O classificador de som não diagnostica saúde. O servidor Jataí mantém a inferência desativada. Não misture espécies ou reutilize modelos de Apis em jataí sem validação específica. Grave origem, espécie, instalação, datas e método dos rótulos. Janelas próximas são correlacionadas; o número de blocos não equivale a observações independentes.

Valide com `python run.py test`. Alguns testes de integração dependem de arquivos externos e são ignorados quando eles não existem. Veja [estrutura e fluxos](README_PROJETO.md) e [inicialização](../README.md).
