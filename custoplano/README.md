# Custo Plano — site e sistema de planejamento de obras (Python)

Servidor em **Python (Flask)** com:

- site institucional + Área do cliente com **login de verdade** (senhas criptografadas);
- banco de dados **SQLite** (um arquivo `custoplano.db`), com o cronograma e as medições de cada cliente;
- leitura do cronograma **direto do .mpp** (biblioteca MPXJ) ou de um .xml salvo pelo MS Project;
- **medição** por tarefa, avanço físico ponderado pelo campo **Peso** do seu cronograma (ou pela duração), comparado com o previsto pela linha de base;
- **exportações** a cada medição: `.xml` que o MS Project abre direto, boletim `.csv` (lido pela macro) e boletim **Excel**;
- **histórico** de todas as medições fechadas, com download de cada uma.

## Estrutura

```
custoplano/
├── app.py            servidor web (rotas, login, API)
├── cronograma.py     leitura do .mpp/.xml, pesos, medição e exportações
├── plano_ataque.py   leitura da planilha do plano de ataque (escadinha)
├── relatorio.py      relatório mensal em PDF
├── custos.py         orçamento com composições, cronograma financeiro e orçado × realizado
├── banco.py          banco de dados SQLite
├── web/index.html    site + telas do sistema (HTML/CSS/JS)
├── macro/CustoPlano.bas   macro do MS Project (grava a medição no seu .mpp)
├── exemplo/cronograma_exemplo.xml   cronograma para teste
├── requirements.txt
└── Dockerfile        para publicar na internet
```

## Instalar e rodar no seu computador (Windows)

1. Instale o **Python 3.10 ou mais novo** (python.org — marque "Add Python to PATH").
2. Instale o **Java 17** (adoptium.net — Temurin 17). Só é necessário para ler `.mpp`.
3. Abra o **Prompt de Comando** na pasta `custoplano` e rode:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

4. Abra **http://localhost:5000** no navegador.
   Clique em **Área do cliente → Ver obra de demonstração** (e-mail `demo@custoplano.com.br`, senha `demo`).

## Criar o acesso de um cliente

```bat
flask --app app criar-usuario cliente@construtora.com.br --nome "Residencial Rubi"
flask --app app trocar-senha cliente@construtora.com.br
flask --app app listar-usuarios
```

Cada usuário vê só o próprio cronograma e o próprio histórico de medições.
Para desligar a obra de demonstração: defina a variável `CP_DEMO=0`.

## Ciclo de medição

1. **Project → Importar cronograma** e escolha o seu `.mpp`. A EAP, as datas, a linha de base e o campo de peso vêm do arquivo.
2. **Medir avanço**: digite o % de cada tarefa (Enter vai para a próxima) e ajuste a **data de status**.
3. Baixe o resultado:
   - **Exportar para o MS Project (.xml)**: abra no Project (Arquivo → Abrir) e salve como `.mpp`; ou
   - **Boletim (.csv)** + macro **ReceberMedicaoCustoPlano** no seu `.mpp` (grava os percentuais sem trocar de arquivo); ou
   - **Boletim em Excel** para relatório.
4. **Fechar medição**: guarda no histórico e a próxima medição parte daí.

### Macro do MS Project
No Project: **Alt+F11 → Arquivo → Importar arquivo → `macro/CustoPlano.bas`** e salve no **Global.MPT**.
- `EnviarParaCustoPlano`: gera uma cópia `.xml` do cronograma aberto (útil se o servidor estiver sem Java).
- `ReceberMedicaoCustoPlano`: lê o boletim `.csv` e grava % concluído, % físico e data de status no `.mpp`.

> Arquivos `.mpp` só podem ser **gravados** pelo MS Project. Por isso a volta da medição é pelo `.xml`, que o Project abre direto, ou pela macro, que grava no seu próprio `.mpp`.

## Custos (orçamento, cronograma financeiro, orçado × realizado)

1. **Custos → Baixar o modelo** e preencha as abas **Orçamento** (código, descrição, unidade, quantidade,
   composição e a EAP da tarefa do cronograma), **Composições** (insumos com tipo, coeficiente e preço),
   **Realizado** (custos pagos, opcional) e **Parâmetros** (BDI). Ou teste com **o orçamento de exemplo**.
2. **Importar orçamento (.xlsx)**. O custo de cada item é distribuído pelos dias da tarefa ligada
   (linha de base do cronograma) e o valor agregado sai do % físico medido.
3. Abas **Orçamento** (com a composição de cada item), **Financeiro** (desembolso mensal e acumulado,
   tabela etapa × mês) e **Orçado × real** (previsto, agregado, realizado, variação e IDC por etapa),
   com o formulário para **lançar custos** pelo sistema. **Exportar para Excel** gera o cronograma financeiro.
4. O controle usa o custo direto; o BDI aparece como preço de venda. O relatório mensal em PDF inclui os custos.

## Medição pelo celular e modo offline

- Menu **Medição**: lista das tarefas com botões grandes (−10, +10, 0/25/50/75/100%), filtro “A medir”
  (já deveriam ter começado pela linha de base e não chegaram a 100%) e busca por tarefa, EAP ou pavimento.
- **Sem internet** a medição fica guardada no aparelho e é enviada sozinha quando a conexão volta.
  O site vira um app instalável (no celular: menu do navegador → *Adicionar à tela inicial*); a página,
  o cronograma e o plano de ataque ficam disponíveis offline depois do primeiro acesso.
- Exportações, fechar medição, relatório e importações precisam de conexão.

## Relatório mensal (PDF)

Botão **Relatório mensal (PDF)** nas telas Project, Medição e Plano de ataque: resumo da medição, Curva S
(prevista pela linha de base × real das medições fechadas), avanço por etapa, tarefas com maior impacto no
atraso, resumo do plano de ataque com marcos e campos para observações e assinaturas.

## Plano de ataque (escadinha)

Programação dos serviços pavimento por pavimento: a forma em tabela da linha de balanço.

1. **Plano de ataque → Importar planilha** e escolha a planilha da escadinha (.xlsx).
2. O sistema lê a linha **Serviço** (nomes), **Fornecedor**, **Ciclo** e um pavimento por linha, com a data de
   cada serviço e **ok** na coluna ao lado quando foi executado. Os blocos marcados **Linha de Base** viram a
   comparação; **DATA BASE**, **Controle de prazo – datas marco**, **Término da obra** e **Meta trimestral** também são lidos.
3. Veja a **matriz** (cores por mês, executado em verde, atrasado em vermelho) ou o **gráfico** (linha de balanço),
   destaque um serviço e ligue **Comparar com a linha de base** para ver o desvio em dias de cada pacote.

## Publicar na internet (custoplano.com.br)

O site precisa de um servidor que rode Python **e Java** (para o `.mpp`). O jeito mais simples é com o `Dockerfile`:

- **Render.com** ou **Railway.app**: crie um serviço "Web Service" a partir desta pasta (via GitHub), com um **disco persistente** montado em `/dados` (é onde fica o banco). Defina as variáveis:
  - `CP_SECRET` = uma frase longa e aleatória (chave das sessões)
  - `CP_HTTPS=1`
  - `CP_DEMO=0` (se não quiser a obra de demonstração)
- Depois aponte o domínio `custoplano.com.br` para o serviço (o painel do Render/Railway mostra os registros DNS a cadastrar no Registro.br).

Em servidor Windows próprio: `pip install waitress` e `waitress-serve --port=8000 app:app`.

## Observações

- Faça cópia de segurança do arquivo `custoplano.db` (ele guarda usuários, cronogramas e medições).
- As telas Linha de balanço, Curto prazo, Suprimentos e Restrições ainda usam dados de exemplo; o cronograma real e as medições (tela Project) já são gravados no banco.
