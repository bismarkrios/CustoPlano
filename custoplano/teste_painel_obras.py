"""Painel das obras (Administração) e aviso de medição atrasada por e-mail.
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_painel_obras.py
"""
import datetime as dt
import io
import os

os.environ["CP_ADMIN_EMAIL"] = ""
import app  # noqa: E402
import banco  # noqa: E402
import correio  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def entrar(email):
    c = app.app.test_client()
    assert c.post("/api/login", json={"email": email, "senha": "senha-forte-123"}).status_code == 200
    return c


banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
obra = banco.criar_obra("Obra Cliente", "Construtora X")
teste = banco.criar_obra("Obra Interna", "Custo Plano")
eng = banco.criar_usuario("eng@construtora.com", "senha-forte-123", "Eng")
leitor = banco.criar_usuario("dono@construtora.com", "senha-forte-123", "Dono")
banco.definir_acesso(eng, obra, "editor")
banco.definir_acesso(leitor, obra, "leitor")
banco.definir_acesso(eng, teste, "editor")

xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()
e = entrar("eng@construtora.com")
for o in (obra, teste):
    assert e.post("/api/obra/abrir", json={"id": o}).status_code == 200
    assert e.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "c.xml")}, content_type="multipart/form-data").status_code == 200
velha = (dt.date.today() - dt.timedelta(days=20)).isoformat()
with banco.conectar() as con:
    con.execute("UPDATE cronogramas SET data_status = ? WHERE usuario_id IN (?, ?)", (velha, obra, teste))

# painel: só a equipe; mostra real x previsto, dias sem medição e o que falta
checar(e.get("/api/admin/painel-obras").status_code == 403, "cliente abriu o painel das obras")
a = entrar("equipe@teste.com")
d = a.get("/api/admin/painel-obras").get_json()
p = {o["obra"]: o for o in d["obras"]}
checar(set(p) >= {"Obra Cliente", "Obra Interna"}, f"obras no painel: {list(p)}")
oc = p.get("Obra Cliente", {})
checar(oc.get("dias_sem_medicao") == 20, f"dias sem medição: {oc.get('dias_sem_medicao')}")
checar(isinstance(oc.get("real"), float) and isinstance(oc.get("previsto"), float), f"real x previsto: {oc}")
checar("Falta importar o Orçado x Realizado" in oc.get("faltas", []), f"faltas: {oc.get('faltas')}")
checar(any("medição física parada" in x for x in oc.get("alertas", [])), f"alertas: {oc.get('alertas')}")
checar(p.get("Obra Interna", {}).get("teste") is True and d["email"] is False, "obra interna ou e-mail")

# aviso: sem e-mail configurado não manda nada
checar(app._avisar_medicoes_atrasadas() == 0, "mandou sem e-mail configurado")
enviados = []
correio.configurado = lambda: {"servidor": "x"}
correio.enviar = lambda para, assunto, texto: enviados.append((para, assunto, texto))
os.environ["CP_ADMIN_EMAIL"] = "contato@custoplano.com.br"
os.environ["CP_AVISO_CLIENTE"] = "1"
app._avisar_medicoes_atrasadas()
paras = sorted(x[0] for x in enviados)
checar(paras == ["contato@custoplano.com.br", "eng@construtora.com"], f"destinatários: {paras}")  # leitor e obra interna fora
checar(all("Obra Cliente: medição atrasada há 20 dias" == x[1] for x in enviados), f"assunto: {[x[1] for x in enviados]}")
checar(all("custoplano.com.br/#entrar" in x[2] and "senha" not in x[2].lower() for x in enviados), "texto do aviso")
app._avisar_medicoes_atrasadas()
checar(len(enviados) == 2, "repetiu o aviso no mesmo dia")
app._avisar_medicoes_atrasadas(dt.date.today() + dt.timedelta(days=7))
checar(len(enviados) == 4, f"não repetiu depois de 7 dias: {len(enviados)}")
os.environ.pop("CP_AVISO_CLIENTE")
enviados.clear()
app._avisar_medicoes_atrasadas(dt.date.today() + dt.timedelta(days=14))
checar([x[0] for x in enviados] == ["contato@custoplano.com.br"], f"sem CP_AVISO_CLIENTE o cliente recebeu: {enviados}")
os.environ["CP_AVISO_CLIENTE"] = "1"

# medição em dia: sem aviso
enviados.clear()
with banco.conectar() as con:
    con.execute("UPDATE cronogramas SET data_status = ? WHERE usuario_id = ?", (dt.date.today().isoformat(), obra))
    con.execute("DELETE FROM avisos_medicao")
app._avisar_medicoes_atrasadas()
checar(not enviados, f"avisou obra em dia: {enviados}")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("painel das obras e aviso de medição ok")
