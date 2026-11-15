"""Medição feita no MS Project conta como medição fechada ao importar o arquivo.
Rodar com:  CP_BANCO=/tmp/p/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_medicao_project.py
"""
import io
import os


import app  # noqa: E402
import banco  # noqa: E402

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


banco.criar_usuario("eng@construtora.com", "senha-forte-123", "Eng")
obra = banco.criar_obra("Obra Cliente", "Construtora X")
banco.definir_acesso(banco.usuario_por_email("eng@construtora.com")["id"], obra, "editor")
c = app.app.test_client()
assert c.post("/api/login", json={"email": "eng@construtora.com", "senha": "senha-forte-123"}).status_code == 200
assert c.post("/api/obra/abrir", json={"id": obra}).status_code == 200

base = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), encoding="utf-8").read()


def versao(data_status, avancar=0):
    """O exemplo com data de status e, opcionalmente, as primeiras N tarefas a 0% passando a 50%."""
    x = base.replace("<CurrentDate>", f"<StatusDate>{data_status}T17:00:00</StatusDate><CurrentDate>", 1)
    for _ in range(avancar):
        x = x.replace("<PercentComplete>0<", "<PercentComplete>50<", 1)
    return x


def importar(xml):
    r = c.post("/api/importar", data={"arquivo": (io.BytesIO(xml.encode()), "obra.xml")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()


def hist():
    return banco.historico(obra)


importar(versao("2026-11-02"))
checar(len(hist()) == 1, f"1ª importação com avanço não virou medição ({len(hist())})")
checar(str(hist()[0]["data_status"])[:10] == "2026-11-02", "data da medição errada")
real1 = hist()[0]["avanco_real"]

importar(versao("2026-11-02"))
checar(len(hist()) == 1, "reenviar o mesmo arquivo duplicou a medição")

importar(versao("2026-11-09"))
checar(len(hist()) == 1, "data nova sem avanço novo virou medição")

importar(versao("2026-11-16", avancar=13))
checar(len(hist()) == 2, f"medição nova do Project não entrou no histórico ({len(hist())})")
checar(hist()[0]["avanco_real"] > real1, "o avanço da medição nova não subiu")
cr = banco.cronograma(obra)
checar(cr["anterior"] == cr["atual"], "depois da medição a anterior não virou a atual")

importar(versao("2026-11-10", avancar=5))
checar(len(hist()) == 2, "arquivo com data de status mais velha virou medição")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("medição do Project ok")
