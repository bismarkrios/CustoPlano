"""E-mail de saída: Gmail API pela conta de serviço (sem senha) e SMTP.
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_correio.py
"""
import base64
import email
import os

for k in ("CP_SMTP_SERVIDOR", "CP_SMTP_USUARIO", "CP_SMTP_SENHA", "CP_DRIVE_CHAVE", "CP_DRIVE_USUARIO"):
    os.environ.pop(k, None)
import correio  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


checar(correio.configurado() is None, "sem nada configurado o e-mail apareceu como ligado")

# chave inválida (ou escopo não liberado): fica desligado, sem derrubar nada
os.environ["CP_DRIVE_CHAVE"] = '{"type": "service_account"}'
os.environ["CP_DRIVE_USUARIO"] = "contato@exemplo.com.br"
checar(correio.gmail_pronto(forcar=True) is False, "chave inválida foi aceita")
checar(correio.configurado() is None, "Gmail não liberado apareceu como ligado")

# escopo liberado: envia pela Gmail API como contato@
enviados = []


class SessaoFalsa:
    def post(self, url, json=None, timeout=None):
        enviados.append((url, json))
        return type("R", (), {"status_code": 200, "text": "{}"})()


correio._gmail.update(ok=True, quando=__import__("time").time())
correio._sessao_gmail = SessaoFalsa
c = correio.configurado()
checar(bool(c and c.get("gmail")), "Gmail liberado não ligou o e-mail")
correio.enviar("eng@construtora.com", "Obra: medição atrasada há 12 dias", "Olá.\nTexto com acentuação: medição.")
checar(len(enviados) == 1 and enviados[0][0].endswith("/users/me/messages/send"), "não chamou a Gmail API")
m = email.message_from_bytes(base64.urlsafe_b64decode(enviados[0][1]["raw"]))
checar(m["To"] == "eng@construtora.com", "destinatário errado")
checar("contato@exemplo.com.br" in m["From"], "remetente não é a conta do Workspace")
checar("medição" in m.get_payload(decode=True).decode(), "texto perdeu a acentuação")

# limite de 50 por dia, com registro no log sem o e-mail inteiro
import contextlib, io  # noqa: E401,E402
saida = io.StringIO()
with contextlib.redirect_stdout(saida):
    for i in range(correio.LIMITE_DIA - 1):
        correio.enviar("eng@construtora.com", "teste", "x")
    try:
        correio.enviar("eng@construtora.com", "teste", "x")
        checar(False, "passou do limite diário de e-mails")
    except RuntimeError:
        pass
log = saida.getvalue()
checar(len(enviados) == correio.LIMITE_DIA, f"enviou {len(enviados)} em vez de {correio.LIMITE_DIA}")
checar("EMAIL-ENVIADO: en***@construtora.com · teste" in log, "EMAIL-ENVIADO não foi para o log")
checar("eng@construtora.com" not in log, "o log tem o e-mail inteiro")
checar("ALERTA-EMAIL" in log, "o limite não gerou alerta no log")

# SMTP preenchido tem preferência
os.environ.update(CP_SMTP_SERVIDOR="smtp.exemplo.com", CP_SMTP_USUARIO="u@exemplo.com", CP_SMTP_SENHA="x")
checar(not correio.configurado().get("gmail"), "SMTP preenchido não teve preferência")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("e-mail de saída ok")
