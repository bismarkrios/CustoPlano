"""WhatsApp da Custo Plano (WhatsApp Business Platform, API da Meta).

Quem está cadastrado numa obra manda "relatório" para o número da Custo Plano e recebe o PDF da obra,
o mesmo do botão do sistema. As chaves ficam só nas variáveis de ambiente do Render:
  WA_TOKEN         token permanente do usuário do sistema (Meta Business)
  WA_PHONE_ID      id do número de telefone no WhatsApp Manager
  WA_VERIFY_TOKEN  frase combinada para a Meta confirmar o endereço do webhook
  WA_APP_SECRET    chave secreta do app da Meta: confere a assinatura de cada aviso recebido
Sem as quatro, o webhook fica desligado.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
import uuid

API = "https://graph.facebook.com/v21.0"


def configurado() -> bool:
    return all(os.environ.get(k) for k in ("WA_TOKEN", "WA_PHONE_ID", "WA_VERIFY_TOKEN", "WA_APP_SECRET"))


def assinatura_ok(corpo: bytes, cabecalho: str) -> bool:
    """X-Hub-Signature-256 = sha256=HMAC(corpo, WA_APP_SECRET): só a Meta conhece a chave."""
    seg = os.environ.get("WA_APP_SECRET", "")
    if not seg or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(seg.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho[7:])


def normalizar(numero: str) -> str:
    """Só dígitos, com 55 na frente quando vier no formato brasileiro (DDD + número)."""
    d = re.sub(r"\D", "", numero or "")
    if len(d) in (10, 11):
        d = "55" + d
    return d


def chave(numero: str) -> str:
    """Forma de comparar números: no Brasil a Meta às vezes manda o celular sem o 9 da frente."""
    d = normalizar(numero)
    if d.startswith("55") and len(d) == 13 and d[4] == "9":
        d = d[:4] + d[5:]
    return d


# ------------------------------------------------------------------ envio
def _pedido(url: str, corpo: bytes, tipo: str) -> dict:
    r = urllib.request.Request(url, data=corpo, method="POST", headers={
        "Authorization": "Bearer " + os.environ["WA_TOKEN"], "Content-Type": tipo})
    with urllib.request.urlopen(r, timeout=30) as resp:  # noqa: S310 (endereço fixo da Meta)
        return json.loads(resp.read() or b"{}")


def _mensagem(para: str, dados: dict) -> dict:
    corpo = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": para, **dados}
    return _pedido(f"{API}/{os.environ['WA_PHONE_ID']}/messages", json.dumps(corpo).encode(), "application/json")


def enviar_texto(para: str, texto: str) -> dict:
    return _mensagem(para, {"type": "text", "text": {"body": texto[:4000]}})


def enviar_pdf(para: str, pdf: bytes, nome: str, legenda: str = "") -> dict:
    """Sobe o PDF para a Meta e manda como documento."""
    b = uuid.uuid4().hex
    partes = [
        f'--{b}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="type"\r\n\r\napplication/pdf\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{nome}"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),
        pdf, f"\r\n--{b}--\r\n".encode(),
    ]
    m = _pedido(f"{API}/{os.environ['WA_PHONE_ID']}/media", b"".join(partes), f"multipart/form-data; boundary={b}")
    return _mensagem(para, {"type": "document", "document": {"id": m["id"], "filename": nome, "caption": legenda[:1000]}})


# ------------------------------------------------------------------ conversa
_VISTAS: dict[str, float] = {}   # id das mensagens já atendidas (a Meta pode reenviar o mesmo aviso)
_PDFS: dict[str, list[float]] = {}
MAX_PDFS_HORA = 10


def mensagens(aviso: dict):
    """(número, id, texto) de cada mensagem de texto do aviso da Meta."""
    for e in aviso.get("entry") or []:
        for c in e.get("changes") or []:
            for m in (c.get("value") or {}).get("messages") or []:
                txt = (m.get("text") or {}).get("body") or (m.get("button") or {}).get("text") or ""
                yield str(m.get("from") or ""), str(m.get("id") or ""), txt.strip()


def ja_vista(mid: str) -> bool:
    agora = time.time()
    for k in [k for k, t in _VISTAS.items() if agora - t > 86400]:
        del _VISTAS[k]
    if mid in _VISTAS:
        return True
    _VISTAS[mid] = agora
    return False


def pode_pdf(numero: str) -> bool:
    agora = time.time()
    ts = [t for t in _PDFS.get(numero, []) if agora - t < 3600]
    _PDFS[numero] = ts
    if len(ts) >= MAX_PDFS_HORA:
        return False
    ts.append(agora)
    return True


AJUDA = "Olá! Aqui é a Custo Plano. Para receber o relatório da sua obra em PDF, mande: relatório"


def responder(numero: str, texto: str, usuario, obras: list[dict], gerar) -> list[tuple]:
    """Decide a resposta. Devolve a lista do que enviar: ("texto", msg) ou ("pdf", obra).
    gerar(obra_id) devolve (pdf, nome) ou None quando a obra ainda não tem dados."""
    t = texto.lower()
    if not usuario:
        return [("texto", "Este número não está cadastrado no sistema da Custo Plano. "
                          "Peça para a Custo Plano incluir o seu WhatsApp no seu acesso.")]
    if not obras:
        return [("texto", "O seu acesso ainda não tem obra liberada. Fale com a Custo Plano.")]
    escolha = None
    if re.fullmatch(r"\d{1,2}", t) and len(obras) > 1:
        n = int(t)
        if not 1 <= n <= len(obras):
            return [("texto", f"Responda com um número de 1 a {len(obras)}.")]
        escolha = obras[n - 1]
    elif re.search(r"relat|pdf|medi", t):
        if len(obras) == 1:
            escolha = obras[0]
        else:
            lista = "\n".join(f"{i}. {o['nome']}" + (f" · {o['empresa']}" if o.get("empresa") else "") for i, o in enumerate(obras, 1))
            return [("texto", "De qual obra? Responda com o número:\n" + lista)]
    if not escolha:
        return [("texto", AJUDA)]
    if not pode_pdf(chave(numero)):
        return [("texto", "Você já pediu vários relatórios na última hora. Tente de novo mais tarde.")]
    r = gerar(escolha["id"])
    if not r:
        return [("texto", f"A obra {escolha['nome']} ainda não tem cronograma, plano de ataque nem orçamento no sistema.")]
    return [("pdf", escolha, r)]
