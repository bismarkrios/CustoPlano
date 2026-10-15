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

## Obras e acessos dos clientes

Tudo pelo sistema: entre com a conta da equipe (a da variável `CP_ADMIN_EMAIL`) e abra
**Área do cliente > Administração**.

- **Obras:** cada obra guarda o próprio cronograma, medições, custos, suprimentos e qualidade.
- **Pessoas:** crie o acesso com o e-mail e escolha a obra. O sistema gera um link (vale 7 dias,
  uso único) para a pessoa criar a própria senha; mande pelo WhatsApp. Uma pessoa pode ter
  várias obras e troca de obra na Área do cliente.
- **Edita ou só consulta:** quem só consulta vê tudo e baixa relatórios, mas não grava nada
  (o servidor recusa).
- **Esqueceu a senha:** botão "Link de senha" na Administração. Com o e-mail de saída
  configurado (abaixo), o cliente também pede o link sozinho em "Esqueci minha senha".

Pela linha de comando (no Shell do Render) ainda existem `criar-usuario` (com `--admin` para a
equipe), `trocar-senha` e `listar-usuarios`.
Para desligar a obra de demonstração: defina a variável `CP_DEMO=0`.

## Cópias de segurança

- O servidor faz uma cópia completa do banco (todas as obras, com fotos) por dia em
  `/dados/copias` e guarda as 7 últimas, sem passar de 40% do disco.
- Antes de apagar uma obra, e antes de converter o banco para obras, sai uma cópia extra
  (com "-antes-" no nome).
- Administração > **Baixar cópia agora** gera um `.db.gz` atual. Baixe uma por semana e guarde
  fora do servidor (computador ou OneDrive).
- Para restaurar: descompacte o `.db.gz`, pare o serviço e troque `/dados/custoplano.db` pelo
  arquivo descompactado (Shell do Render), depois reinicie.

## E-mail de saída (esqueci minha senha)

Desligado até as variáveis serem definidas no Render (nunca no código):
`CP_SMTP_SERVIDOR=smtp.office365.com`, `CP_SMTP_PORTA=587`,
`CP_SMTP_USUARIO=contato@custoplano.com.br`, `CP_SMTP_SENHA=<senha da conta ou de aplicativo>`.
No Microsoft 365 é preciso ligar o "SMTP autenticado" da caixa contato@.
`CP_SITE=https://custoplano.com.br` faz os links usarem o endereço certo.

## Saúde do sistema

`/saude` responde 200 quando o servidor e o banco estão de pé. Use no Render
(Settings > Health Check Path) e num monitor externo gratuito (ex.: UptimeRobot) para receber
aviso se o sistema sair do ar.

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
