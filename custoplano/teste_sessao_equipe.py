"""Login da equipe vale 12 h (cliente continua 7 dias) e toda entrada da equipe vai para o log (ALERTA-LOGIN).
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_sessao_equipe.py
"""
import contextlib
import io
import os
import time

os.environ["CP_ADMIN_EMAIL"] = ""
import app  # noqa: E402
import banco  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
banco.criar_usuario("cliente@construtora.com", "senha-forte-123", "Cliente")

eq, cli = app.app.test_client(), app.app.test_client()
saida = io.StringIO()
with contextlib.redirect_stdout(saida):
    checar(eq.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"}).status_code == 200, "equipe não entrou")
    checar(cli.post("/api/login", json={"email": "cliente@construtora.com", "senha": "senha-forte-123"}).status_code == 200, "cliente não entrou")
log = saida.getvalue()
checar("ALERTA-LOGIN: entrada da equipe eq***@teste.com" in log, f"entrada da equipe sem alerta: {log!r}")
checar("cliente" not in log, "entrada de cliente foi para o alerta")
checar("equipe@teste.com" not in log, "o log tem o e-mail inteiro")

with eq.session_transaction() as s:
    checar(abs(s["ate"] - (time.time() + 12 * 3600)) < 60, "sessão da equipe não ficou com 12 h")
with cli.session_transaction() as s:
    checar("ate" not in s, "cliente ganhou o limite de 12 h")

ROTA = "/api/me"
checar(eq.get(ROTA).get_json()["email"] == "equipe@teste.com", "equipe caiu antes das 12 h")
with eq.session_transaction() as s:
    s["ate"] = time.time() - 1
checar(eq.get(ROTA).get_json()["email"] is None, "equipe continuou logada depois de 12 h")
checar(cli.get(ROTA).get_json()["email"] == "cliente@construtora.com", "cliente caiu junto")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("sessão da equipe ok")
