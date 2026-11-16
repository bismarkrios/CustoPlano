"""E-mail de saída do sistema (links de troca de senha, aviso de medição atrasada).

Dois caminhos, sem senha nenhuma no código:
1. Gmail API pela conta de serviço que já faz as cópias no Drive (CP_DRIVE_CHAVE e CP_DRIVE_USUARIO),
   enviando como contato@. Liga sozinho quando o escopo gmail.send é liberado na delegação em todo o
   domínio (Admin do Google > Segurança > Controles de API). Enquanto não for liberado, fica desligado.
2. SMTP, se as variáveis CP_SMTP_* forem preenchidas no painel do Render (usado no lugar do Gmail). Com o
Microsoft 365: CP_SMTP_SERVIDOR=smtp.office365.com, CP_SMTP_PORTA=587,
CP_SMTP_USUARIO=contato@custoplano.com.br e a senha de aplicativo em CP_SMTP_SENHA.
A senha nunca fica no código nem no navegador.
"""
from __future__ import annotations

import base64
import json
import os
import smtplib
import time
from email.message import EmailMessage


ESCOPO_GMAIL = "https://www.googleapis.com/auth/gmail.send"
_gmail = {"ok": None, "quando": 0.0}  # resultado da última checagem do escopo (vale 1 hora)


def _cred_gmail():
    from google.oauth2 import service_account

    info = json.loads(os.environ["CP_DRIVE_CHAVE"])
    cred = service_account.Credentials.from_service_account_info(info, scopes=[ESCOPO_GMAIL])
    return cred.with_subject(os.environ["CP_DRIVE_USUARIO"].strip())


def gmail_pronto(forcar: bool = False) -> bool:
    """O escopo gmail.send está liberado? Pede um token (não envia nada). Guarda a resposta por 1 hora."""
    if not (os.environ.get("CP_DRIVE_CHAVE", "").strip() and os.environ.get("CP_DRIVE_USUARIO", "").strip()):
        return False
    if not forcar and _gmail["ok"] is not None and time.time() - _gmail["quando"] < 3600:
        return bool(_gmail["ok"])
    try:
        from google.auth.transport.requests import Request

        _cred_gmail().refresh(Request())
        ok = True
    except Exception as e:  # noqa: BLE001
        if _gmail["ok"] is not False:
            print(f"EMAIL: Gmail ainda não liberado ({type(e).__name__}: {str(e)[:120]})", flush=True)
        ok = False
    _gmail.update(ok=ok, quando=time.time())
    return ok


def configurado() -> dict | None:
    c = {k: os.environ.get("CP_SMTP_" + k.upper(), "") for k in ("servidor", "usuario", "senha", "remetente")}
    if c["servidor"] and c["usuario"] and c["senha"]:
        c["porta"] = int(os.environ.get("CP_SMTP_PORTA") or 587)
        c["remetente"] = c["remetente"] or c["usuario"]
        return c
    if gmail_pronto():
        return {"gmail": True, "remetente": os.environ["CP_DRIVE_USUARIO"].strip()}
    return None


def enviar(para: str, assunto: str, texto: str) -> None:
    c = configurado()
    if not c:
        raise RuntimeError("e-mail de saída não configurado")
    msg = EmailMessage()
    msg["From"] = f"Custo Plano <{c['remetente']}>"
    msg["To"] = para
    msg["Subject"] = assunto
    msg.set_content(texto)
    if c.get("gmail"):
        from google.auth.transport.requests import AuthorizedSession

        bruto = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = AuthorizedSession(_cred_gmail()).post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": bruto}, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Gmail recusou o envio ({r.status_code}): {r.text[:200]}")
        return
    with smtplib.SMTP(c["servidor"], c["porta"], timeout=20) as s:
        s.starttls()
        s.login(c["usuario"], c["senha"])
        s.send_message(msg)
