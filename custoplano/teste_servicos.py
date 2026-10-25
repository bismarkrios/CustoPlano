"""Teste dos serviços contratados por obra: o sistema só libera as rotas e os relatórios de
planejamento, custos ou qualidade que a construtora contratou.
Rodar com:  CP_BANCO=/tmp/servicos.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_servicos.py
"""
import io
import os
import sys

import app
import banco

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
adm = app.app.test_client()
assert adm.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"}).status_code == 200

# obra nova sem dizer os serviços: contrata os três (como as obras que já existiam)
o_todos = adm.post("/api/admin/obra", json={"nome": "Obra completa"}).get_json()["id"]
checar(banco.servicos_da_obra(o_todos) == ["planejamento", "custos", "qualidade"], "obra nova não veio com os três serviços")
# obra só de qualidade e obra só de custos
o_q = adm.post("/api/admin/obra", json={"nome": "Só qualidade", "servicos": ["qualidade"]}).get_json()["id"]
o_c = adm.post("/api/admin/obra", json={"nome": "Só custos", "servicos": ["custos", "inventado"]}).get_json()["id"]
checar(banco.servicos_da_obra(o_c) == ["custos"], "aceitou serviço inventado")
checar(adm.post("/api/admin/obra", json={"nome": "Nada", "servicos": []}).status_code == 400, "criou obra sem serviço")
checar(adm.post("/api/admin/obra/servicos", json={"id": o_q, "servicos": []}).status_code == 400, "tirou todos os serviços")

xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()


def abrir(obra_id):
    r = adm.post("/api/obra/abrir", json={"id": obra_id})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# só qualidade: qualidade funciona; custos, plano e curto prazo não
conta = abrir(o_q)
checar(conta["obra"]["servicos"] == ["qualidade"], "a conta não traz os serviços da obra aberta")
checar(adm.get("/api/qualidade").status_code == 200, "qualidade bloqueada numa obra de qualidade")
checar(adm.get("/api/custos").status_code == 403, "custos liberado numa obra só de qualidade")
checar(adm.get("/api/plano").status_code == 403, "plano liberado numa obra só de qualidade")
checar(adm.get("/api/curtoprazo").status_code == 403, "curto prazo liberado numa obra só de qualidade")
checar(adm.get("/api/relatorio.pdf?tipo=custos").status_code == 403, "relatório de custos liberado numa obra só de qualidade")
r = adm.get("/api/relatorio.pdf?tipo=qualidade")
checar(r.status_code == 200 and r.data[:4] == b"%PDF", "relatório de qualidade não saiu")
r = adm.get("/api/relatorio.pdf")
checar(r.status_code == 200 and r.data[:4] == b"%PDF", "consolidado da obra só de qualidade não saiu")

# só custos: cronograma e medição (base do valor agregado) e custos funcionam; qualidade e plano não
abrir(o_c)
r = adm.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "obra.xml")}, content_type="multipart/form-data")
checar(r.status_code == 200, "obra só de custos não conseguiu importar o cronograma")
checar(adm.get("/api/historico").status_code == 200, "medição bloqueada numa obra de custos")
checar(adm.post("/api/custos/exemplo").status_code == 200, "custos bloqueado numa obra de custos")
checar(adm.get("/api/suprimentos").status_code == 200, "suprimentos bloqueado numa obra de custos")
checar(adm.get("/api/qualidade").status_code == 403, "qualidade liberada numa obra só de custos")
checar(adm.get("/api/plano").status_code == 403, "plano liberado numa obra só de custos")
checar(adm.get("/api/relatorio.pdf?tipo=prazo").status_code == 403, "relatório de prazo liberado numa obra só de custos")
checar(adm.get("/api/relatorio.pdf?tipo=custos").status_code == 200, "relatório de custos não saiu")

# incluir um serviço depois libera na hora
checar(adm.post("/api/admin/obra/servicos", json={"id": o_c, "servicos": ["custos", "qualidade"]}).status_code == 200,
       "não conseguiu incluir qualidade")
checar(adm.get("/api/qualidade").status_code == 200, "qualidade continuou bloqueada depois de incluída")
checar(adm.get("/api/me").get_json()["obra"]["servicos"] == ["custos", "qualidade"], "a conta não mostra o serviço incluído")

# a obra completa continua com tudo
abrir(o_todos)
for rota in ("/api/qualidade", "/api/custos", "/api/plano", "/api/curtoprazo", "/api/suprimentos"):
    checar(adm.get(rota).status_code == 200, f"{rota} bloqueada numa obra com os três serviços")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("serviços contratados OK")
