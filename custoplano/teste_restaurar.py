"""Restauração guiada (Administração): volta o sistema para uma cópia do servidor ou do Drive.
Confere: pede RESTAURAR, guarda o banco de antes, recusa arquivo que não é cópia do sistema,
só a equipe usa, e a cópia cifrada do Drive volta inteira.
Rodar com:  CP_BANCO=/tmp/r/t.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_restaurar.py
"""
import gzip
import os

import app
import banco
import copia_drive

falhas = []


def obras():
    with banco.conectar() as con:
        return [r[0] for r in con.execute("SELECT nome FROM obras ORDER BY id")]


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


adm = banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
cli = banco.criar_usuario("cliente@teste.com", "senha-forte-123", "Cliente")
banco.definir_acesso(cli, banco.criar_obra("Obra 1"), "editor")
copia = os.path.basename(banco.copia_de_seguranca())          # foto do sistema antes da Obra 2
N = len(obras())
banco.criar_obra("Obra 2 (feita depois da cópia)")

a, c = app.app.test_client(), app.app.test_client()
for cl, em in ((a, "equipe@teste.com"), (c, "cliente@teste.com")):
    assert cl.post("/api/login", json={"email": em, "senha": "senha-forte-123"}).status_code == 200

checar(c.get("/api/admin/restaurar/lista").status_code == 403, "cliente viu a lista de restauração")
checar(c.post("/api/admin/restaurar", json={"origem": "servidor", "nome": copia, "confirmar": "RESTAURAR"}).status_code == 403,
       "cliente conseguiu restaurar")
lista = a.get("/api/admin/restaurar/lista").get_json()
checar(copia in [x["nome"] for x in lista["servidor"]] and lista["drive"] == [], f"lista: {lista}")

r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": copia, "confirmar": "sim"})
checar(r.status_code == 400 and len(obras()) == N + 1, "restaurou sem digitar RESTAURAR")
r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": "../../etc/passwd", "confirmar": "RESTAURAR"})
checar(r.status_code == 404, "aceitou caminho fora da pasta de cópias")

# arquivo que não é cópia do sistema: recusa e não mexe em nada
falsa = os.path.join(banco.pasta_copias(), "custoplano-2026-01-01-0000.db.gz")
open(falsa, "wb").write(gzip.compress(b"nao e banco"))
r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": os.path.basename(falsa), "confirmar": "RESTAURAR"})
checar(r.status_code == 400 and len(obras()) == N + 1, f"aceitou cópia falsa: {r.status_code}")
os.remove(falsa)

r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": copia, "confirmar": "restaurar"})
j = r.get_json()
checar(r.status_code == 200 and j["obras"] == N, f"restauração do servidor: {r.status_code} {j}")
checar("Obra 2 (feita depois da cópia)" not in obras() and len(obras()) == N, f"obras depois: {obras()}")
checar(any("antes-da-restauracao" in x["nome"] for x in banco.listar_copias()), "não guardou o banco de antes")
with banco.conectar() as con:  # tabelas novas continuam existindo depois de voltar uma cópia
    con.execute("SELECT COUNT(*) FROM registros_acesso").fetchone()

# desfazer: volta a cópia "antes" e a Obra 2 reaparece
antes = j["antes"]
r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": antes, "confirmar": "RESTAURAR"})
if r.status_code == 401:  # a sessão pode cair na troca; entra de novo
    a.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"})
    r = a.post("/api/admin/restaurar", json={"origem": "servidor", "nome": antes, "confirmar": "RESTAURAR"})
checar(r.status_code == 200 and len(obras()) == N + 1, f"desfazer: {r.status_code}")

# Drive: cópia cifrada volta inteira; id que não está na pasta é recusado
seg = "segredo-de-teste"
gz = os.path.join(banco.pasta_copias(), copia)
cif = gz + ".cp"
copia_drive.cifrar(gz, cif, seg)
conteudo = open(cif, "rb").read()
os.remove(cif)


class Resp:
    def __init__(self, js=None, content=b""):
        self.status_code, self._js, self.content = 200, js or {}, content

    def json(self):
        return self._js

    def raise_for_status(self):
        pass


class Drive:
    def get(self, url, params=None, timeout=None):
        if params.get("alt") == "media":
            return Resp(content=conteudo)
        if "folder" in params["q"]:
            return Resp({"files": [{"id": "P1"}]})
        return Resp({"files": [{"id": "ARQ1", "name": copia + ".cp"}, {"id": "X", "name": "outra.db.gz"}]})


copia_drive._sessao = lambda: Drive()
os.environ.update(CP_DRIVE_CHAVE="{}", CP_DRIVE_USUARIO="contato@exemplo.com", CP_DRIVE_CRIPTO=seg)
lista = a.get("/api/admin/restaurar/lista").get_json()
checar([x["id"] for x in lista["drive"]] == ["ARQ1"], f"lista do Drive: {lista['drive']}")
r = a.post("/api/admin/restaurar", json={"origem": "drive", "id": "OUTRO", "confirmar": "RESTAURAR"})
checar(r.status_code == 400 and len(obras()) == N + 1, "aceitou id fora da pasta do Drive")
os.environ["CP_DRIVE_CRIPTO"] = "segredo-errado"
r = a.post("/api/admin/restaurar", json={"origem": "drive", "id": "ARQ1", "confirmar": "RESTAURAR"})
checar(r.status_code == 400 and len(obras()) == N + 1, "restaurou com segredo errado")
os.environ["CP_DRIVE_CRIPTO"] = seg
r = a.post("/api/admin/restaurar", json={"origem": "drive", "id": "ARQ1", "confirmar": "RESTAURAR"})
if r.status_code == 401:
    a.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"})
    r = a.post("/api/admin/restaurar", json={"origem": "drive", "id": "ARQ1", "confirmar": "RESTAURAR"})
checar(r.status_code == 200 and "Obra 2 (feita depois da cópia)" not in obras() and len(obras()) == N, f"restauração do Drive: {r.status_code} {r.get_json()}")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("restauração guiada ok")
