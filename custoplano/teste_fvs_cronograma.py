"""Teste das FVS ligadas às tarefas do cronograma (escolha manual, sem FVS, volta ao automático e isolamento).
Rodar com:  CP_BANCO=/tmp/fvs.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_fvs_cronograma.py
"""
import sys

import app
import banco

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def entrar(email, senha="senha-forte-123"):
    c = app.app.test_client()
    r = c.post("/api/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, (email, r.get_json())
    return c


banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
adm = entrar("equipe@teste.com")
o1 = adm.post("/api/admin/obra", json={"nome": "Torre Norte"}).get_json()["id"]
o2 = adm.post("/api/admin/obra", json={"nome": "Casa Sul"}).get_json()["id"]
adm.post("/api/obra/abrir", json={"id": o1})

q = adm.get("/api/qualidade").get_json()
checar(q["tarefas"] == {}, f"tarefas deveriam começar vazias: {q['tarefas']}")
m1 = q["modelos"][0]["id"]
r = adm.post("/api/qualidade/tarefa", json={"uid": "12", "modelo_id": m1})
checar(r.status_code == 200 and r.get_json()["tarefas"] == {"12": {"modelo_id": m1}}, f"não ligou a FVS: {r.get_json()}")
r = adm.post("/api/qualidade/tarefa", json={"uid": "13", "modelo_id": 0})
checar(r.get_json()["tarefas"].get("13") == {"modelo_id": 0}, "não marcou sem FVS")
r = adm.post("/api/qualidade/tarefa", json={"uid": "12", "modelo_id": None})
checar("12" not in r.get_json()["tarefas"], "não voltou para o automático")
checar(adm.post("/api/qualidade/tarefa", json={"uid": "abc", "modelo_id": m1}).status_code == 400, "aceitou tarefa inválida")
checar(adm.post("/api/qualidade/tarefa", json={"uid": "5", "modelo_id": "x"}).status_code == 400, "aceitou FVS inválida")

# FVS de outra obra não pode ser ligada, e as ligações não vazam entre obras
adm.post("/api/obra/abrir", json={"id": o2})
q2 = adm.get("/api/qualidade").get_json()
checar(q2["tarefas"] == {}, f"ligação vazou para outra obra: {q2['tarefas']}")
checar(adm.post("/api/qualidade/tarefa", json={"uid": "5", "modelo_id": m1}).status_code == 400, "aceitou FVS de outra obra")

# quem só consulta não altera
r = adm.post("/api/admin/usuario", json={"email": "leitor@x.com", "acessos": [{"obra_id": o1, "papel": "leitor"}]})
cod = r.get_json()["link"].split("#senha=")[1]
app.app.test_client().post("/api/senha/definir", json={"codigo": cod, "senha": "senha-forte-123"})
lei = entrar("leitor@x.com")
checar(lei.post("/api/qualidade/tarefa", json={"uid": "14", "modelo_id": 0}).status_code in (401, 403), "leitor alterou a FVS da tarefa")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("FVS do cronograma ok")
