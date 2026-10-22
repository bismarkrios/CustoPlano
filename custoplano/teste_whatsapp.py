"""Teste do WhatsApp que manda o relatório: webhook desligado sem as chaves, assinatura da Meta,
cadastro do número na Administração e respostas (número desconhecido, uma obra, várias obras).
O envio para a Meta é trocado por uma lista, nada sai da máquina.
Rodar com:  CP_BANCO=/tmp/wa.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_whatsapp.py
"""
import hashlib
import hmac
import io
import json
import os
import sys
import threading

import app
import banco
import whatsapp as wa

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


cli = app.app.test_client()
checar(cli.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1").status_code == 404, "webhook ligado sem as chaves")
checar(cli.post("/whatsapp/webhook", data=b"{}").status_code == 404, "aviso aceito sem as chaves")

os.environ.update(WA_TOKEN="t", WA_PHONE_ID="123", WA_VERIFY_TOKEN="frase-combinada", WA_APP_SECRET="segredo")
r = cli.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=frase-combinada&hub.challenge=987")
checar(r.status_code == 200 and r.data == b"987", "não confirmou o webhook com a frase certa")
checar(cli.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=errada&hub.challenge=1").status_code == 403, "confirmou com a frase errada")

# números: com e sem o 9, com máscara
checar(wa.normalizar("(11) 98823-6792") == "5511988236792", "normalizar")
checar(wa.chave("5511988236792") == wa.chave("551188236792"), "celular sem o 9 não bate")

# ---- equipe cadastra obras, pessoas e números
banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
adm = app.app.test_client()
adm.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"})
o1 = adm.post("/api/admin/obra", json={"nome": "Torre Norte", "empresa": "X"}).get_json()["id"]
o2 = adm.post("/api/admin/obra", json={"nome": "Casa Sul", "empresa": "Y"}).get_json()["id"]
eng = adm.post("/api/admin/usuario", json={"email": "eng@x.com", "acessos": [{"obra_id": o1, "papel": "editor"}]}).get_json()["id"]
dono = adm.post("/api/admin/usuario", json={"email": "dono@x.com", "acessos": [{"obra_id": o1, "papel": "leitor"}, {"obra_id": o2, "papel": "leitor"}]}).get_json()["id"]
r = adm.post("/api/admin/whatsapp", json={"usuario_id": eng, "numero": "(11) 97777-1111"})
checar(r.status_code == 200 and r.get_json()["whatsapp"] == "5511977771111", "não gravou o WhatsApp")
checar(adm.post("/api/admin/whatsapp", json={"usuario_id": dono, "numero": "11 7777-1111"}).status_code == 400, "aceitou número curto")
checar(adm.post("/api/admin/whatsapp", json={"usuario_id": dono, "numero": "5511977771111"}).status_code == 400, "aceitou número repetido")
adm.post("/api/admin/whatsapp", json={"usuario_id": dono, "numero": "11 96666-2222"})
checar(app.app.test_client().post("/api/admin/whatsapp", json={"usuario_id": eng, "numero": ""}).status_code in (401, 403), "sem login mudou o número")
painel = {u["email"]: u for u in adm.get("/api/admin").get_json()["usuarios"]}
checar(painel["eng@x.com"]["whatsapp"] == "5511977771111", "painel não mostra o WhatsApp")

# cronograma na obra 1
adm.post("/api/obra/abrir", json={"id": o1})
xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()
checar(adm.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "torre.xml")}, content_type="multipart/form-data").status_code == 200, "não importou")

# ---- avisos da Meta (envio trocado por lista; o atendimento roda na hora, sem thread)
enviados = []
wa.enviar_texto = lambda para, t: enviados.append((para, "texto", t))
wa.enviar_pdf = lambda para, pdf, nome, leg="": enviados.append((para, "pdf", nome, pdf[:4]))
app.threading = type("T", (), {"Thread": lambda target, args, **k: type("X", (), {"start": lambda s: target(*args)})()})


def aviso(numero, texto, mid):
    corpo = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": numero, "id": mid, "type": "text", "text": {"body": texto}}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
    return cli.post("/whatsapp/webhook", data=corpo, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


corpo = b'{"entry":[]}'
checar(cli.post("/whatsapp/webhook", data=corpo, headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 403, "aceitou assinatura falsa")

aviso("5511955550000", "relatório", "m1")
checar(enviados and "não está cadastrado" in enviados[-1][2], "número desconhecido recebeu outra coisa")

enviados.clear(); aviso("551177771111", "oi", "m2")  # sem o 9, como a Meta às vezes manda
checar(enviados and "relatório" in enviados[-1][2] and enviados[-1][1] == "texto", "não mandou a ajuda")
enviados.clear(); aviso("551177771111", "Relatório", "m3")
checar(enviados and enviados[-1][1] == "pdf" and enviados[-1][3] == b"%PDF" and "relatorio" in enviados[-1][2], f"não mandou o PDF: {enviados}")
enviados.clear(); aviso("551177771111", "Relatório", "m3")
checar(not enviados, "respondeu duas vezes a mesma mensagem")

enviados.clear(); aviso("5511966662222", "quero o pdf", "m4")
checar(enviados and "De qual obra" in enviados[-1][2] and "2. Casa Sul" in enviados[-1][2], f"não perguntou a obra: {enviados}")
enviados.clear(); aviso("5511966662222", "2", "m5")
checar(enviados and "ainda não tem" in enviados[-1][2], "obra sem dados não avisou")
enviados.clear(); aviso("5511966662222", "1", "m6")
checar(enviados and enviados[-1][1] == "pdf", "não mandou o PDF da obra escolhida")
enviados.clear(); aviso("5511966662222", "7", "m7")
checar(enviados and "de 1 a 2" in enviados[-1][2], "aceitou obra fora da lista")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("whatsapp OK")
