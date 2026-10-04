"""Teste de isolamento entre contas: cada usuário só enxerga e altera os próprios dados.

Faz o papel das políticas de RLS: a conta B tenta ler e alterar tudo o que a conta A
criou (cronograma, medição, plano de ataque, custos, inspeções, fotos e não conformidades).
Rodar com:  CP_BANCO=/tmp/iso.db CP_SECRET=x python teste_isolamento.py
"""
import io
import os
import sys

import app
import banco

FOTO = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def entrar(email):
    if not any(u["email"] == email for u in banco.listar_usuarios()):
        banco.criar_usuario(email, "senha-forte-123", email)
    c = app.app.test_client()
    assert c.post("/api/login", json={"email": email, "senha": "senha-forte-123"}).status_code == 200
    return c


a, b = entrar("a@teste.com"), entrar("b@teste.com")

# ---- conta A cria dados de todos os tipos
xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()
assert a.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "obra_a.xml")}, content_type="multipart/form-data").status_code == 200
a.post("/api/fechar")
mid = a.get("/api/historico").get_json()[0]["id"]
assert a.post("/api/custos/exemplo").status_code == 200
qa = a.get("/api/qualidade").get_json()
mod = qa["modelos"][0]
itens = [{"id": i.get("id"), "item": i.get("item"), "criterio": i.get("criterio"), "status": "nc", "obs": "x", "fotos": [FOTO]}
         for i in mod["itens"]]
r = a.post("/api/qualidade/inspecao", json={"modelo_id": mod["id"], "local": "Pav A", "data": "2026-10-01", "itens": itens})
assert r.status_code == 200, r.get_json()
qa = a.get("/api/qualidade").get_json()
insp = qa["inspecoes"][0]
foto_a = insp["itens"][0]["fotos"][0]
nc_a = qa["ncs"][0]

# ---- conta B não vê nada disso
checar(b.get("/api/cronograma").get_json()["xml"] is None, "B leu o cronograma de A")
checar(b.get("/api/plano").get_json()["plano"] is None, "B leu o plano de ataque de A")
checar(b.get("/api/custos").get_json()["custos"] is None, "B leu os custos de A")
checar(not b.get("/api/historico").get_json(), "B leu o histórico de medições de A")
checar(b.get(f"/api/historico/{mid}/xlsx").status_code == 404, "B baixou uma medição fechada de A")
checar(b.get(f"/api/qualidade/foto/{foto_a}").status_code == 404, "B abriu uma foto de A")
checar(b.get(f"/api/qualidade/inspecao/{insp['id']}.pdf").status_code == 404, "B baixou o PDF de uma inspeção de A")
qb = b.get("/api/qualidade").get_json()
checar(not qb["inspecoes"] and not qb["ncs"], "B leu inspeções ou não conformidades de A")
checar(all(m["id"] != mod["id"] for m in qb["modelos"]), "B leu os modelos de FVS de A")
for rota in ("/api/relatorio.pdf", "/api/exportar/xml", "/api/exportar/xlsx", "/api/custos/financeiro.xlsx"):
    checar(b.get(rota).status_code in (404, 400), f"B baixou {rota} com dados de A")

# ---- conta B não altera nem apaga nada de A
b.post("/api/qualidade/nc", json={"id": nc_a["id"], "descricao": "alterada por B", "status": "fechada", "acao": "x", "eficacia": "x"})
b.post("/api/qualidade/inspecao/remover", json={"id": insp["id"]})
b.post("/api/qualidade/modelo/remover", json={"id": mod["id"]})
b.post("/api/qualidade/inspecao", json={"modelo_id": mod["id"], "local": "Pav B", "data": "2026-10-01",
                                        "itens": [{"item": "x", "status": "ok", "fotos": [foto_a]}]})
qa2 = a.get("/api/qualidade").get_json()
checar(any(n["id"] == nc_a["id"] and n["descricao"] == nc_a["descricao"] for n in qa2["ncs"]), "B alterou uma NC de A")
checar(any(i["id"] == insp["id"] for i in qa2["inspecoes"]), "B apagou uma inspeção de A")
checar(any(m["id"] == mod["id"] for m in qa2["modelos"]), "B apagou um modelo de FVS de A")
qb2 = b.get("/api/qualidade").get_json()
checar(all(foto_a not in it["fotos"] for i in qb2["inspecoes"] for it in i["itens"]), "B anexou uma foto de A")
b.post("/api/remover"); b.post("/api/custos/remover"); b.post("/api/plano/remover")
checar(a.get("/api/cronograma").get_json()["xml"] is not None, "B apagou o cronograma de A")
checar(a.get("/api/custos").get_json()["custos"] is not None, "B apagou os custos de A")

# ---- sem login, nada
anon = app.app.test_client()
for rota in ("/api/cronograma", "/api/plano", "/api/custos", "/api/qualidade", "/api/historico", f"/api/qualidade/foto/{foto_a}"):
    checar(anon.get(rota).status_code == 401, f"sem login acessou {rota}")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("isolamento entre contas OK")
