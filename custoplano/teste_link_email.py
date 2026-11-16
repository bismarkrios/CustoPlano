"""Ao criar um acesso (ou pedir link novo), o link de senha vai direto para o e-mail cadastrado.
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_link_email.py
"""
import os

os.environ["CP_ADMIN_EMAIL"] = ""
import app  # noqa: E402
import banco  # noqa: E402
import correio  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
a = app.app.test_client()
assert a.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"}).status_code == 200

# sem e-mail ligado: devolve o link para copiar, enviado = False
r = a.post("/api/admin/usuario", json={"email": "ana@construtora.com", "nome": "Ana"}).get_json()
checar(r.get("link") and r.get("enviado") is False, "sem e-mail ligado devia só devolver o link")

# com e-mail ligado: manda para o e-mail cadastrado
enviados = []
correio.configurado = lambda: {"gmail": True, "remetente": "contato@exemplo.com.br"}
correio.enviar = lambda para, assunto, texto: enviados.append((para, assunto, texto))
r = a.post("/api/admin/usuario", json={"email": "Bruno@Construtora.com", "nome": "Bruno"}).get_json()
checar(r.get("enviado") is True, "acesso novo não foi por e-mail")
checar(enviados and enviados[-1][0] == "bruno@construtora.com", "e-mail foi para o endereço errado")
checar(enviados and r["link"] in enviados[-1][2] and "Bruno" in enviados[-1][2], "o e-mail não leva o link e o nome")

uid = banco.usuario_por_email("ana@construtora.com")["id"]
r = a.post("/api/admin/link-senha", json={"usuario_id": uid}).get_json()
checar(r.get("enviado") is True and enviados[-1][0] == "ana@construtora.com", "link novo de senha não foi por e-mail")


def falha(*_):
    raise RuntimeError("caiu")


correio.enviar = falha
r = a.post("/api/admin/link-senha", json={"usuario_id": uid}).get_json()
checar(r.get("link") and r.get("enviado") is False, "falha no envio devia cair para o link na tela")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("link de acesso por e-mail ok")
