"""Orçado x Realizado mês a mês: se o cliente ligar, cada importação fica guardada no mês dela.
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_custos_mes_a_mes.py
"""
import os

os.environ["CP_ADMIN_EMAIL"] = ""
import app  # noqa: E402
import banco  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


obra = banco.criar_obra("Obra Mês a Mês", "Construtora")
banco.criar_usuario("eng@construtora.com", "senha-forte-123", "Eng")
uid = banco.usuario_por_email("eng@construtora.com")["id"]
banco.definir_acesso(uid, obra, "editor")
c = app.app.test_client()
assert c.post("/api/login", json={"email": "eng@construtora.com", "senha": "senha-forte-123"}).status_code == 200


def ctl(contratado):
    return {"linhas": [{"codigo": "01", "servico": "Estrutura", "oin": 1000, "oatu": 1000, "contratado": contratado,
                        "incorrido": contratado / 2, "saldo_contrato": 0, "saldo_pedido": 0, "a_contratar": 1000 - contratado}]}


# desligado (padrão): importar não guarda mês
banco.salvar_controle_custos(obra, "out.xlsx", ctl(400))
r = c.get("/api/custos/meses").get_json()
checar(r["ligado"] is False and r["meses"] == [], f"guardou mês sem o cliente ligar: {r}")

# ligar guarda o mês atual na hora
r = c.post("/api/custos/meses", json={"ligado": True}).get_json()
checar(r["ligado"] and len(r["meses"]) == 1, f"ligar não guardou o mês atual: {r}")
mes = r["meses"][0]["mes"]
checar(abs(r["meses"][0]["contratado"] - 400) < 0.01, f"resumo do mês errado: {r['meses'][0]}")

# nova importação no mesmo mês substitui o mês
banco.salvar_controle_custos(obra, "out2.xlsx", ctl(500))
r = c.get("/api/custos/meses").get_json()
checar(len(r["meses"]) == 1 and r["meses"][0]["arquivo"] == "out2.xlsx", f"mesmo mês duplicou: {r}")

# mês anterior fica intacto quando chega um mês novo
with banco.conectar() as con:
    con.execute("UPDATE controle_custos_meses SET mes = '2026-10' WHERE usuario_id = ?", (obra,))
banco.salvar_controle_custos(obra, "nov.xlsx", ctl(700))
r = c.get("/api/custos/meses").get_json()
checar([m["mes"] for m in r["meses"]] == [mes, "2026-10"], f"meses: {[m['mes'] for m in r['meses']]}")
checar(abs(r["meses"][1]["contratado"] - 500) < 0.01, "o mês anterior mudou com a importação nova")

# abrir um mês guardado mostra aquele fechamento
a = c.get("/api/custos?mes=2026-10").get_json()["custos"]
checar(a.get("mes") == "2026-10" and abs(a["controle"]["total"]["contratado"] - 500) < 0.01, "não abriu o mês guardado")
a = c.get("/api/custos").get_json()["custos"]
checar(not a.get("mes") and abs(a["controle"]["total"]["contratado"] - 700) < 0.01, "a tela atual não ficou com a última importação")
checar(c.get("/api/custos?mes=../x").get_json()["custos"].get("mes") is None, "aceitou mês inválido")

# leitor não liga nem desliga
banco.criar_usuario("leitor@construtora.com", "senha-forte-123", "Leitor")
lid = banco.usuario_por_email("leitor@construtora.com")["id"]
banco.definir_acesso(lid, obra, "leitor")
l = app.app.test_client()
l.post("/api/login", json={"email": "leitor@construtora.com", "senha": "senha-forte-123"})
checar(l.post("/api/custos/meses", json={"ligado": False}).status_code == 403, "leitor desligou o mês a mês")
checar(len(l.get("/api/custos/meses").get_json()["meses"]) == 2, "leitor não vê os meses")

# outra obra não enxerga os meses desta
obra2 = banco.criar_obra("Outra", "X")
banco.criar_usuario("outro@x.com", "senha-forte-123", "Outro")
banco.definir_acesso(banco.usuario_por_email("outro@x.com")["id"], obra2, "editor")
o = app.app.test_client()
o.post("/api/login", json={"email": "outro@x.com", "senha": "senha-forte-123"})
checar(o.get("/api/custos/meses").get_json()["meses"] == [], "outra obra viu os meses")
checar(o.get("/api/custos?mes=2026-10").get_json()["custos"] is None, "outra obra abriu o mês guardado")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("custos mês a mês ok")
