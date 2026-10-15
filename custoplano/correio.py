"""E-mail de saída do sistema (links de troca de senha).

Fica desligado até as variáveis CP_SMTP_* serem preenchidas no painel do Render. Com o
Microsoft 365: CP_SMTP_SERVIDOR=smtp.office365.com, CP_SMTP_PORTA=587,
CP_SMTP_USUARIO=contato@custoplano.com.br e a senha de aplicativo em CP_SMTP_SENHA.
A senha nunca fica no código nem no navegador.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def configurado() -> dict | None:
    c = {k: os.environ.get("CP_SMTP_" + k.upper(), "") for k in ("servidor", "usuario", "senha", "remetente")}
    if not (c["servidor"] and c["usuario"] and c["senha"]):
        return None
    c["porta"] = int(os.environ.get("CP_SMTP_PORTA") or 587)
    c["remetente"] = c["remetente"] or c["usuario"]
    return c


def enviar(para: str, assunto: str, texto: str) -> None:
    c = configurado()
    if not c:
        raise RuntimeError("e-mail de saída não configurado")
    msg = EmailMessage()
    msg["From"] = f"Custo Plano <{c['remetente']}>"
    msg["To"] = para
    msg["Subject"] = assunto
    msg.set_content(texto)
    with smtplib.SMTP(c["servidor"], c["porta"], timeout=20) as s:
        s.starttls()
        s.login(c["usuario"], c["senha"])
        s.send_message(msg)
