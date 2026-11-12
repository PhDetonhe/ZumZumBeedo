> Guia detalhado preservado. Para os passos atuais, consulte o README.md da raiz e docs/README_PROJETO.md. Caminhos citados em tabelas antigas identificam módulos agora em src/ ou telas em web/templates/.

# BeeGuard Monitor — atualização do painel

Requer a entrega anterior funcionando: beeguard_server.py, audio_pipeline.py, capture_to_server.py e MySQL configurado. Extraia este ZIP diretamente na pasta BeeGuard. Os arquivos anteriores e dados são preservados. Nenhuma dependência nova.

## Instalação no terminal integrado PowerShell, dentro de BeeGuard

Pare somente o servidor antigo com Ctrl+C. O coletor pode continuar; durante a troca ele mantém a fila.

```powershell
.\.venv\Scripts\python.exe run.py beeguard_monitor --init-db
.\.venv\Scripts\python.exe -m unittest test_monitor -v
.\.venv\Scripts\python.exe run.py beeguard_monitor
```

Abra http://127.0.0.1:5000 e use Ctrl+F5. Agora inicie sempre beeguard_monitor.py no lugar de beeguard_server.py. O módulo antigo permanece como dependência.

Se o coletor não estiver rodando, em outro terminal:

```powershell
.\.venv\Scripts\python.exe run.py capture_to_server --hive BG01 --port COM6
```

## Operação

1. Em uma colmeia real, mantenha o microfone no mesmo local e com o mesmo ganho. Para testes de bancada, os indicadores descrevem a bancada, não abelhas.
2. Observe um período habitual por 30 minutos. Clique em “Definir referência de 30 min”. A confirmação é sobre sua observação de campo. O servidor exige cobertura e sinal adequado, mas não consegue confirmar normalidade biológica.
3. O gráfico usa medianas de RMS por minuto. A métrica recente usa os últimos 5 minutos. A comparação em dB é relativa à amplitude RMS digital, não dB SPL de sonômetro.
4. Registre obra, manejo, clima e inspeção com data, horário, atividade observada e descrição. Use o dia do histórico para ver eventos antigos. A interface usa São Paulo; o banco armazena UTC. Datas históricas anteriores a 2020 não devem usar este formulário com offset fixo -03:00 devido ao antigo horário de verão.
5. Dados do gráfico/diário correspondem ao período selecionado; os cartões de intensidade/alerta sempre descrevem a captura atual, mesmo ao consultar um dia passado.

## Regras iniciais experimentais

- Sem captura nos últimos 60 s → verificar USB/coletor/alimentação. Não é baixa atividade.
- Mais de 20% de gravações recentes saturadas/inválidas → verificar qualidade; não inferir atividade.
- Mediana recente pelo menos +6 dB da referência → intensidade elevada, investigar fontes.
- Mediana recente pelo menos -6 dB → intensidade reduzida, acompanhar.
- Todos os minutos de 30 min abaixo de -6 dB, ao menos 120 gravações válidas, 30 minutos cobertos, >=80% de gravações utilizáveis, sem lacunas >60 s e referência definida há pelo menos 30 min → redução persistente, inspecionar colmeia e sensor.
- Valores próximos à referência não significam saúde normal.

Referência guarda mediana dos últimos 30 min, exige ao menos 25 minutos cobertos/120 gravações, sem lacunas >60 s, dados recentes e RMS >=0,0001. É fixa: redefina após troca de sensor, ganho, posição ou mudança de contexto. Histórico de referências fica salvo. Sinal quase silencioso é incluído na detecção de queda, mas clipping >1% é excluído. Nenhum limiar foi validado para prever abandono.

## Dados e limitações

Tabelas existentes de áudio são preservadas; duas novas tabelas MySQL: `beeguard_observations` e `beeguard_references`. WAVs continuam em `data/live/audio/`. Diário registra fatos/observações, sem declarar causalidade. Registros de bancada BG01 já existentes permanecem; crie um identificador novo ao instalar na colmeia real, por exemplo `--hive COLMEIA01`, para separar o contexto.

Modelo de população/diagnóstico continua desativado. Este pacote não treina IA nem separa automaticamente ruído de obra de zumbido. Para evoluir: coletar áudio de colmeias reais e observações consistentes; guardar temperatura/umidade/horário; avaliar detecção de som de abelhas e ruído externo; medir força da colmeia em inspeções; validar em outras colmeias e datas. Áudio ambiente e vibração mecânica são medições distintas. Um segundo microfone externo pode ajudar a estudar interferências, mas não está implementado.

Gráfico relativo a uma referência global fixa não corrige sazonalidade/horário/clima. Som baixo não confirma abandono; som alto não confirma agitação das abelhas ou condição ambiental específica. Não há confiança percentual inventada. Testes locais usam repositório em memória; tabelas novas precisam ser verificadas no MySQL do usuário.

Serviço permanece local em 127.0.0.1. Para desfazer, pare o monitor e execute o servidor anterior; tabelas adicionadas podem permanecer.
