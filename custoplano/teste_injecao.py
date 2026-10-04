"""Teste contra SQL injection.

1) Verificação no código: toda consulta usa parâmetros (?) e nenhum SQL é montado com
   texto vindo do usuário. SQL montado com f-string só passa se a linha estiver marcada
   com "sql-seguro" (nomes de coluna fixos no código).
2) Ataque de verdade: envia cargas clássicas de injeção em login, campos de texto, ids e
   URLs, e confere que o login não é burlado, que o texto é gravado como texto, que as
   tabelas continuam lá e que nenhuma resposta é erro 500.
Rodar com:  CP_BANCO=/tmp/inj.db CP_SECRET=x python teste_injecao.py
"""
import glob
import os
import re
import sys

import app
import banco

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


# ---- 1) código
for arq in glob.glob(os.path.join(app.AQUI, "*.py")):
    if os.path.basename(arq).startswith("teste_"):
        continue
    texto = open(arq, encoding="utf-8").read()
    nome = os.path.basename(arq)
    # f-string logo depois de execute( (na mesma linha ou na seguinte)
    for m in re.finditer(r"\.execute(?:many|script)?\((?P<coment>[ \t]*#[^\n]*)?\s*f[\"']", texto):
        if "sql-seguro" not in (m.group("coment") or ""):
            checar(False, f"{nome}:{texto[:m.start()].count(chr(10)) + 1} monta SQL com f-string")
    # SQL concatenado ou formatado com % / + / .format
    for m in re.finditer(r"\.execute(?:many)?\(\s*[\"'][^\"'\n]*[\"']\s*(?:%|\+|\.format)", texto):
        checar(False, f"{nome}:{texto[:m.start()].count(chr(10)) + 1} monta SQL concatenando texto")

# ---- 2) ataque
CARGAS = ["' OR '1'='1", "' OR 1=1 --", "admin'--", "\"; DROP TABLE usuarios; --", "1; DELETE FROM ncs",
          "x') UNION SELECT email, senha_hash FROM usuarios --", "%' OR email LIKE '%"]

banco.criar_usuario("vitima@teste.com", "senha-forte-123", "Vítima")
anon = app.app.test_client()
for c in CARGAS:
    for email, senha in ((c, c), ("vitima@teste.com", c), (c, "senha-forte-123")):
        r = anon.post("/api/login", json={"email": email, "senha": senha})
        checar(r.status_code in (401, 429), f"login com {email!r}/{senha!r} respondeu {r.status_code}")

banco.criar_usuario("atacante@teste.com", "senha-forte-123", "Atacante")
n_usuarios = len(banco.listar_usuarios())
cli = app.app.test_client()
assert cli.post("/api/login", json={"email": "atacante@teste.com", "senha": "senha-forte-123"}).status_code == 200
cli.post("/api/custos/exemplo")
for c in CARGAS:
    respostas = [
        cli.post("/api/qualidade/nc", json={"descricao": c, "servico": c, "local": c, "responsavel": c, "causa": c}),
        cli.post("/api/qualidade/nc", json={"id": c, "descricao": "x"}),
        cli.post("/api/qualidade/inspecao", json={"modelo_id": c, "local": c, "data": "2026-10-01",
                                                  "itens": [{"item": c, "status": "ok", "fotos": [c]}]}),
        cli.post("/api/qualidade/inspecao/remover", json={"id": c}),
        cli.post("/api/qualidade/modelo", json={"id": c, "codigo": c, "servico": c, "itens": [{"item": c}]}),
        cli.post("/api/custos/lancamento", json={"data": "2026-10-01", "valor": 10, "descricao": c, "codigo": c, "fornecedor": c}),
        cli.post("/api/custos/lancamento/remover", json={"id": c}),
        cli.post("/api/medicao", json={"meas": {c: 50}, "statusDate": c, "weight": c}),
        cli.get("/api/qualidade/foto/" + c.replace("/", "")),
        cli.get("/api/historico/1/" + c.replace("/", "")),
    ]
    for r in respostas:
        checar(r.status_code < 500, f"carga {c!r} causou erro {r.status_code} em {r.request.path}")

q = cli.get("/api/qualidade").get_json()
checar(any(n["descricao"] == CARGAS[0] for n in q["ncs"]), "o texto com aspas não foi gravado como texto")
checar(len(banco.listar_usuarios()) == n_usuarios, "a tabela de usuários foi alterada")
cus = cli.get("/api/custos").get_json()["custos"]
checar(any(l["descricao"] == CARGAS[3] for l in cus["lancamentos"]), "o lançamento com SQL no texto não foi gravado como texto")
with banco.conectar() as con:
    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
checar({"usuarios", "cronogramas", "custos", "ncs", "inspecoes", "fotos"} <= tabelas, "alguma tabela sumiu")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("proteção contra SQL injection OK")
